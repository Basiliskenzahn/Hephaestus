// The backend URL (Modal). Empty = static mode: examples replay, live runs and rebuilding are disabled.
// ?api=<url> overrides it (local testing).
window.HEPHAESTUS_API = new URLSearchParams(location.search).get("api") || "";
