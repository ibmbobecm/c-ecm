import com.filenet.api.core.*;
import com.filenet.api.collection.*;
import com.filenet.api.constants.*;
import com.filenet.api.util.UserContext;
import java.io.BufferedReader;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.io.PrintWriter;
import java.net.ServerSocket;
import java.net.Socket;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import javax.security.auth.Subject;

/**
 * Persistent version of FileNetBridge. Each CLI invocation of FileNetBridge
 * pays for a fresh JVM start AND a fresh IIOP connection/CSIv2 handshake to
 * the Content Engine; after enough of those in one session, this
 * installation's connection handling gets congested and some calls start
 * hanging for minutes instead of failing fast (observed directly — the same
 * request is instant on a fresh connection, slow once many have piled up).
 * This process opens ONE Connection at startup and reuses it — and caches
 * the ObjectStore lookup — for every request for the life of the process,
 * so there's no repeated connection setup to contend.
 *
 * Protocol: plain TCP, one line per request, one line per response.
 *   Request:  <user>\t<pass>\t<objectStore>\t<op>\t<arg1>\t<arg2>...\n
 *   Response: OK <result>\n   or   ERR <message>\n
 * Same op set and semantics as FileNetBridge.java's CLI.
 */
public class FileNetBridgeServer {
    private static Connection conn;
    private static final Map<String, ObjectStore> objectStoreCache = new ConcurrentHashMap<>();

    public static void main(String[] args) throws Exception {
        int port = args.length > 0 ? Integer.parseInt(args[0]) : 8021;
        conn = Factory.Connection.getConnection("iiop://localhost:2809/FileNet/Engine");

        ExecutorService pool = Executors.newFixedThreadPool(4);
        ServerSocket server = new ServerSocket(port, 64, java.net.InetAddress.getByName("127.0.0.1"));
        System.out.println("READY " + port);
        System.out.flush();
        while (true) {
            Socket client = server.accept();
            pool.submit(() -> handle(client));
        }
    }

    private static ObjectStore getObjectStore(String storeName) throws Exception {
        ObjectStore cached = objectStoreCache.get(storeName);
        if (cached != null) return cached;
        Domain domain = Factory.Domain.fetchInstance(conn, null, null);
        ObjectStore os = Factory.ObjectStore.fetchInstance(domain, storeName, null);
        objectStoreCache.put(storeName, os);
        return os;
    }

    private static void handle(Socket client) {
        try (Socket c = client;
             BufferedReader in = new BufferedReader(new InputStreamReader(c.getInputStream(), "UTF-8"));
             PrintWriter out = new PrintWriter(new java.io.OutputStreamWriter(c.getOutputStream(), "UTF-8"), true)) {
            String line = in.readLine();
            if (line == null) return;
            String[] parts = line.split("\t", -1);
            if (parts.length < 4) {
                out.println("ERR malformed request");
                return;
            }
            String user = parts[0];
            String pass = parts[1];
            String storeName = parts[2];
            String op = parts[3];

            Subject subject = UserContext.createSubject(conn, user, pass, "FileNetP8");
            UserContext.get().pushSubject(subject);
            try {
                ObjectStore os = getObjectStore(storeName);
                String result = dispatch(os, op, parts);
                out.println("OK " + result);
            } catch (Exception e) {
                out.println("ERR " + e.getClass().getName() + ": " + e.getMessage());
            } finally {
                UserContext.get().popSubject();
            }
        } catch (Exception e) {
            // connection-level failure; nothing more to do
        }
    }

    private static String dispatch(ObjectStore os, String op, String[] a) throws Exception {
        if (op.equals("createDocument")) {
            return createDocument(os, a[4], a[5], a[6], a[7]);
        } else if (op.equals("checkin")) {
            return checkin(os, a[4], a[5], a[6], a[7], Boolean.parseBoolean(a[8]));
        } else if (op.equals("getContent")) {
            return getContent(os, a[4], a[5]);
        } else if (op.equals("unfileDocument")) {
            return unfileDocument(os, a[4]);
        } else if (op.equals("fileDocument")) {
            return fileDocument(os, a[4], a[5], a[6]);
        }
        throw new IllegalArgumentException("unknown op: " + op);
    }

    private static ContentElementList buildContent(String fileName, String mimeType, String localPath) throws Exception {
        InputStream in = new FileInputStream(localPath);
        ContentTransfer ct = Factory.ContentTransfer.createInstance();
        ct.setCaptureSource(in);
        ct.set_RetrievalName(fileName);
        ct.set_ContentType(mimeType);
        ContentElementList cel = Factory.ContentElement.createList();
        cel.add(ct);
        return cel;
    }

    private static String createDocument(ObjectStore os, String folderPath, String fileName, String mimeType, String localPath) throws Exception {
        Document doc = Factory.Document.createInstance(os, null);
        doc.getProperties().putValue("DocumentTitle", fileName);
        doc.set_ContentElements(buildContent(fileName, mimeType, localPath));
        doc.save(RefreshMode.REFRESH);

        Folder folder = Factory.Folder.fetchInstance(os, folderPath, null);
        ReferentialContainmentRelationship rcr = folder.file(doc, AutoUniqueName.NOT_AUTO_UNIQUE, fileName, DefineSecurityParentage.DO_NOT_DEFINE_SECURITY_PARENTAGE);
        rcr.save(RefreshMode.REFRESH);

        return doc.get_Id().toString();
    }

    private static String checkin(ObjectStore os, String documentId, String fileName, String mimeType, String localPath, boolean major) throws Exception {
        Document doc = Factory.Document.fetchInstance(os, documentId, null);
        doc.checkout(ReservationType.EXCLUSIVE, null, null, null);
        Document reservation = (Document) doc.get_Reservation();

        reservation.set_ContentElements(buildContent(fileName, mimeType, localPath));
        reservation.checkin(AutoClassify.DO_NOT_AUTO_CLASSIFY,
                major ? CheckinType.MAJOR_VERSION : CheckinType.MINOR_VERSION);
        reservation.save(RefreshMode.REFRESH);

        return reservation.get_Id().toString();
    }

    private static String getContent(ObjectStore os, String documentId, String outPath) throws Exception {
        Document doc = Factory.Document.fetchInstance(os, documentId, null);
        ContentElementList cel = doc.get_ContentElements();
        OutputStream out = new FileOutputStream(outPath);
        try {
            for (Object o : cel) {
                ContentTransfer ct = (ContentTransfer) o;
                InputStream in = ct.accessContentStream();
                byte[] buf = new byte[8192];
                int n;
                while ((n = in.read(buf)) != -1) {
                    out.write(buf, 0, n);
                }
                in.close();
            }
        } finally {
            out.close();
        }
        return outPath;
    }

    private static String unfileDocument(ObjectStore os, String documentId) throws Exception {
        Document doc = Factory.Document.fetchInstance(os, documentId, null);
        ReferentialContainmentRelationshipSet containers = doc.get_Containers();
        java.util.Iterator it = containers.iterator();
        while (it.hasNext()) {
            ReferentialContainmentRelationship rcr = (ReferentialContainmentRelationship) it.next();
            rcr.delete();
            rcr.save(RefreshMode.NO_REFRESH);
        }
        return documentId;
    }

    private static String fileDocument(ObjectStore os, String documentId, String newFolderPath, String newName) throws Exception {
        Document doc = Factory.Document.fetchInstance(os, documentId, null);
        Folder newFolder = Factory.Folder.fetchInstance(os, newFolderPath, null);
        ReferentialContainmentRelationship rcr = newFolder.file(doc, AutoUniqueName.NOT_AUTO_UNIQUE, newName, DefineSecurityParentage.DO_NOT_DEFINE_SECURITY_PARENTAGE);
        rcr.save(RefreshMode.REFRESH);
        return documentId;
    }
}
