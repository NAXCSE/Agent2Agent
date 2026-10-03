// Runtime configuration for the static UI.
//
// LOCAL DEV / SELF-HOSTED BACKEND: leave API_BASE as "" and the app calls the
// same origin that served the page.
//
// VERCEL: put your Render backend URL here, e.g.
//   window.AGENT2AGENT_API = "https://agent2agent-backend.onrender.com";
// It can also be overridden at runtime with ?api=<url>, which is handy for
// testing a staging backend without editing this file.
window.AGENT2AGENT_API = "";