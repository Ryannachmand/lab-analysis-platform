import { useState } from 'react';
import './Login.css';

export default function Login({ onLogin }) {
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');

  function handleSignIn(e) {
    e.preventDefault();
    const user = { name: name.trim() || 'Researcher', email: email.trim() };
    localStorage.setItem('orion_user', JSON.stringify(user));
    onLogin(user);
  }

  function handleContinueWithout() {
    onLogin(null);
  }

  return (
    <div className="login">
      <div className="login__glow" />
      <div className="login__card">
        <h2 className="login__heading">Welcome back</h2>
        <form className="login__form" onSubmit={handleSignIn}>
          <div className="login__field">
            <label className="login__label">Name</label>
            <input
              className="login__input"
              type="text"
              placeholder="Your name"
              value={name}
              onChange={e => setName(e.target.value)}
              autoFocus
            />
          </div>
          <div className="login__field">
            <label className="login__label">Email <span className="login__optional">(optional)</span></label>
            <input
              className="login__input"
              type="email"
              placeholder="you@example.com"
              value={email}
              onChange={e => setEmail(e.target.value)}
            />
          </div>
          <button type="submit" className="login__btn">
            Sign in
          </button>
        </form>
        <button className="login__skip" onClick={handleContinueWithout}>
          Continue without signing in
        </button>
      </div>
    </div>
  );
}
