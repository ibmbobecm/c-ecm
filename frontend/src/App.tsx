import { useState } from "react";
import "./App.css";
import { AuthProvider, useAuth } from "./contexts/AuthContext";
import { ConnectionsProvider } from "./contexts/ConnectionsContext";
import { Landing } from "./pages/Landing";
import { Login } from "./pages/Login";
import { Drive } from "./pages/Drive";

function Shell() {
  const { user, loading } = useAuth();
  const [showLogin, setShowLogin] = useState(false);

  if (loading) {
    return <div className="boot-loading">Loading...</div>;
  }

  if (!user) {
    return showLogin ? <Login onBack={() => setShowLogin(false)} /> : <Landing onSignIn={() => setShowLogin(true)} />;
  }

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
