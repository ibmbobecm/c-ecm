import com.filenet.api.core.*;
import com.filenet.api.collection.*;
import com.filenet.api.constants.*;
import com.filenet.api.util.UserContext;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import javax.security.auth.Subject;

/**
 * CLI bridge for FileNet content-write operations that are broken over the
 * WSI/SOAP transport in this installation (NPE in PersisterBase on any
 * ContentElements write). Uses the native EJB/IIOP transport instead, which
 * is unaffected. Read-only and non-content operations continue to use the
 * WSI/zeep path in filenet_client.py; only route content-bearing writes here.
 *
 * Usage:
 *   FileNetBridge <iiopUri> <user> <pass> <objectStore> createDocument <folderPath> <fileName> <mimeType> <localFilePath>
 *   FileNetBridge <iiopUri> <user> <pass> <objectStore> checkin <documentId> <fileName> <mimeType> <localFilePath> <major:true|false>
 *   FileNetBridge <iiopUri> <user> <pass> <objectStore> getContent <documentId> <localOutFilePath>
 *
 * <iiopUri> is the target server, e.g. iiop://localhost:2809/FileNet/Engine
 * — different connections can point at entirely different FileNet
 * installations; the JVM's -Djava.naming.provider.url (set by the Python
 * caller) must resolve the same host:port for the JNDI bootstrap to work.
 *
 * Prints a single line "OK <id>" on success, "ERR <message>" on failure (exit code 1).
 */
public class FileNetBridge {
    public static void main(String[] args) throws Exception {
        if (args.length < 5) {
            System.out.println("ERR usage: <iiopUri> <user> <pass> <objectStore> <op> ...");
            System.exit(1);
        }
        String connUri = args[0];
        String user = args[1];
        String pass = args[2];
        String storeName = args[3];
        String op = args[4];

        Connection conn = Factory.Connection.getConnection(connUri);
        Subject subject = UserContext.createSubject(conn, user, pass, "FileNetP8");
        UserContext.get().pushSubject(subject);
        try {
            Domain domain = Factory.Domain.fetchInstance(conn, null, null);
            ObjectStore os = Factory.ObjectStore.fetchInstance(domain, storeName, null);

            if (op.equals("createDocument")) {
                String folderPath = args[5];
                String fileName = args[6];
                String mimeType = args[7];
                String localPath = args[8];
                System.out.println(createDocument(os, folderPath, fileName, mimeType, localPath));
            } else if (op.equals("checkin")) {
                String documentId = args[5];
                String fileName = args[6];
                String mimeType = args[7];
                String localPath = args[8];
                boolean major = Boolean.parseBoolean(args[9]);
                System.out.println(checkin(os, documentId, fileName, mimeType, localPath, major));
            } else if (op.equals("getContent")) {
                String documentId = args[5];
                String outPath = args[6];
                System.out.println(getContent(os, documentId, outPath));
            } else if (op.equals("unfileDocument")) {
                String documentId = args[5];
                System.out.println(unfileDocument(os, documentId));
            } else if (op.equals("fileDocument")) {
                String documentId = args[5];
                String newFolderPath = args[6];
                String newName = args[7];
                System.out.println(fileDocument(os, documentId, newFolderPath, newName));
            } else {
                System.out.println("ERR unknown op: " + op);
                System.exit(1);
            }
        } catch (Exception e) {
            System.out.println("ERR " + e.getClass().getName() + ": " + e.getMessage());
            System.exit(1);
        } finally {
            UserContext.get().popSubject();
        }
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

        return "OK " + doc.get_Id().toString();
    }

    private static String checkin(ObjectStore os, String documentId, String fileName, String mimeType, String localPath, boolean major) throws Exception {
        Document doc = Factory.Document.fetchInstance(os, documentId, null);
        doc.checkout(ReservationType.EXCLUSIVE, null, null, null);
        Document reservation = (Document) doc.get_Reservation();

        reservation.set_ContentElements(buildContent(fileName, mimeType, localPath));
        reservation.checkin(AutoClassify.DO_NOT_AUTO_CLASSIFY,
                major ? CheckinType.MAJOR_VERSION : CheckinType.MINOR_VERSION);
        reservation.save(RefreshMode.REFRESH);

        return "OK " + reservation.get_Id().toString();
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
        return "OK " + outPath;
    }

    // Move is unfile + file. Both steps are fast and reliable in isolation
    // (verified directly), but running them back-to-back in one transaction
    // reliably hangs in this installation — so the Python side calls these
    // as two separate bridge invocations (two separate JVMs/transactions)
    // rather than combining them here.

    private static String unfileDocument(ObjectStore os, String documentId) throws Exception {
        Document doc = Factory.Document.fetchInstance(os, documentId, null);
        ReferentialContainmentRelationshipSet containers = doc.get_Containers();
        java.util.Iterator it = containers.iterator();
        while (it.hasNext()) {
            ReferentialContainmentRelationship rcr = (ReferentialContainmentRelationship) it.next();
            rcr.delete();
            rcr.save(RefreshMode.NO_REFRESH);
        }
        return "OK " + documentId;
    }

    private static String fileDocument(ObjectStore os, String documentId, String newFolderPath, String newName) throws Exception {
        Document doc = Factory.Document.fetchInstance(os, documentId, null);
        Folder newFolder = Factory.Folder.fetchInstance(os, newFolderPath, null);
        ReferentialContainmentRelationship rcr = newFolder.file(doc, AutoUniqueName.NOT_AUTO_UNIQUE, newName, DefineSecurityParentage.DO_NOT_DEFINE_SECURITY_PARENTAGE);
        rcr.save(RefreshMode.REFRESH);
        return "OK " + documentId;
    }
}
