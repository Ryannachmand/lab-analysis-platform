import { useState } from 'react';
import Landing from './screens/Landing';
import Login from './screens/Login';
import Workspace from './screens/Workspace';

export default function App() {
  const [screen, setScreen] = useState('landing');
  const [user, setUser] = useState(() => {
    try {
      const stored = localStorage.getItem('orion_user');
      return stored ? JSON.parse(stored) : null;
    } catch {
      return null;
    }
  });

  function handleLogin(userData) {
    setUser(userData);
    setScreen('workspace');
  }

  if (screen === 'landing') {
    return <Landing onEnter={() => setScreen('login')} />;
  }

  if (screen === 'login') {
    return <Login onLogin={handleLogin} />;
  }

  return <Workspace user={user} />;
}
