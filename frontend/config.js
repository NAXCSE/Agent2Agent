// Which backend the UI talks to.
//
// LOCAL DEV: set this to "" and the app falls back to the page's own origin,
// which is what you want when the same FastAPI server serves the UI.
//
// DEPLOYED: this points at the Render backend.
//
// If the Vercel project has Build Command `node inject-config.js` and an
// AGENT2AGENT_API env var, the build overwrites this file from that variable,
// so the env var is the thing to change when the backend URL moves.
//
// Either way you can test a different backend per visit without editing
// anything: https://<this-app>/?api=https://staging.onrender.com
window.AGENT2AGENT_API = "https://agent2agent.onrender.com";