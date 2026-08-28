import "./App.css";
import { AuthProvider, useAuth } from "./contexts/AuthContext";
import { ConnectionsProvider } from "./contexts/ConnectionsContext";
import { Login } from "./pages/Login";
import { Drive } from "./pages/Drive";

function Shell() {
  const { user, loading } = useAuth();

  if (loading) {
    return <div className="boot-loading">Loading...</div>;
  }

  if (!user) return <Login />;

  return (
    <ConnectionsProvider>
      <Drive />
    </ConnectionsProvider>
  );
}

function App() {
  return (
    <AuthProvider>
      <Shell />
    </AuthProvider>
  );
}

export default App;
