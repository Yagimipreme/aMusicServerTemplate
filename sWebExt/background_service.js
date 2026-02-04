// Cross-Browser API Wrapper
const browserAPI = typeof browser !== "undefined" ? browser : chrome;

browserAPI.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.type === "SEND_TO_SERVER") {
    
    // URL aus dem Storage holen
    browserAPI.storage.sync.get(['lanUrl'], (data) => {
      const targetUrl = data.lanUrl || "http://localhost:5000";

      fetch(targetUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: request.url })
      })
      .then(response => {
        if (!response.ok) throw new Error("Server Error");
        return response.json();
      })
      .then(data => console.log("Success:", data))
      .catch(error => console.error("Error:", error));
    });

    return true; // Hält den Message-Channel offen für asynchrone Antworten
  }
});