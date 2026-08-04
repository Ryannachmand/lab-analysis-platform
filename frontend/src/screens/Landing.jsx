import './Landing.css';

export default function Landing({ onEnter }) {
  return (
    <div className="landing">
      <div className="landing__glow" />
      <div className="landing__content">
        <h1 className="landing__title">Orion</h1>
        <p className="landing__subtitle">Lab Analysis Platform</p>
        <button className="landing__enter-btn" onClick={onEnter}>
          Enter
        </button>
      </div>
    </div>
  );
}
