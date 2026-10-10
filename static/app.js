
const ticketInput = document.getElementById("ticket");
const analyzeBtn = document.getElementById("analyzeBtn");
const errorBox = document.getElementById("error");
const emptyState = document.getElementById("emptyState");
const results = document.getElementById("results");


async function initializeSaaS() {
  try {
    const response = await fetch("/api/auth/me", {
      credentials: "same-origin"
    });

    if (response.status === 401) {
      window.location.href = "/login";
      return;
    }

    if (!response.ok) {
      throw new Error("Could not verify your account.");
    }

    const user = await response.json();
    const label = document.getElementById("companyLabel");

    if (label) {
      label.textContent = `Workspace: ${user.company_name}`;
    }
  } catch (error) {
    console.error("Authentication check failed:", error);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  initializeSaaS();

  const logoutBtn = document.getElementById("logoutBtn");

  if (logoutBtn) {
    logoutBtn.addEventListener("click", async () => {
      logoutBtn.disabled = true;

      try {
        const response = await fetch("/api/auth/logout", {
          method: "POST",
          credentials: "same-origin"
        });

        if (!response.ok) {
          throw new Error("Logout failed.");
        }

        window.location.href = "/login";
      } catch (error) {
        console.error(error);
        alert("Could not log out. Please try again.");
        logoutBtn.disabled = false;
      }
    });
  }
});


document.querySelectorAll(".example").forEach((button) => {
    button.addEventListener("click", () => {
        ticketInput.value = button.dataset.message;
        ticketInput.focus();
        errorBox.textContent = "";
    });
});

function setText(id, value) {
    document.getElementById(id).textContent = value ?? "—";
}

function setBar(id, value) {
    const number = Math.max(0, Math.min(100, Number(value) || 0));
    document.getElementById(id).style.width = `${number}%`;
}

analyzeBtn.addEventListener("click", async () => {
    const message = ticketInput.value.trim();
    errorBox.textContent = "";

    if (!message) {
        errorBox.textContent = "Please enter a customer message first.";
        ticketInput.focus();
        return;
    }

    analyzeBtn.disabled = true;
    analyzeBtn.textContent = "Analyzing ticket...";
    document.getElementById("mode").textContent = "Processing";

    try {
        const response = await fetch("/api/analyze", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message })
        });

        const data = await response.json();

        if (!response.ok || data.error) {
            throw new Error(data.error || "Could not analyze this ticket.");
        }

        setText("priority", data.priority);
        setText("urgency", `${data.urgency_score}/10`);
        setText("department", data.department);
        setText("route", data.department);
        setText(
            "confidence",
            `${Math.round(Number(data.department_confidence || 0) * 100)}%`
        );

        setText("refund", `${Number(data.refund_probability || 0)}%`);
        setText(
            "human",
            `${Number(data.human_review_probability || 0)}%`
        );

        setBar("refundBar", data.refund_probability);
        setBar("humanBar", data.human_review_probability);
        setText("reply", data.suggested_reply);

        const notice = document.getElementById("notice");
        notice.textContent = data.notice || "";
        notice.hidden = !data.notice;

        document.getElementById("mode").textContent =
            data.provider === "Cloudflare Workers AI"
                ? "Live AI"
                : "Demo mode";

        emptyState.hidden = true;
        results.hidden = false;
    } catch (error) {
        errorBox.textContent =
            error.message || "Something went wrong. Please try again.";
        document.getElementById("mode").textContent = "Error";
    } finally {
        analyzeBtn.disabled = false;
        analyzeBtn.innerHTML = "<span>✦</span> Analyze Ticket";
    }
});

document.getElementById("copyBtn").addEventListener("click", async () => {
    const reply = document.getElementById("reply").textContent;

    try {
        await navigator.clipboard.writeText(reply);
        document.getElementById("copyBtn").textContent = "Copied!";
        setTimeout(() => {
            document.getElementById("copyBtn").textContent = "Copy reply";
        }, 1500);
    } catch {
        errorBox.textContent = "Copy failed. Please select and copy the reply.";
    }
});


/* Session analytics and recent ticket history */

const sessionAnalytics = {
    total: 0,
    high: 0,
    urgencyTotal: 0,
    escalations: 0,
    history: []
};

function escapeHTML(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;"
    })[char]);
}

function updateAnalytics(data, message) {
    const urgency = Math.max(
        1,
        Math.min(10, Number(data.urgency_score) || 1)
    );

    const priority = String(data.priority || "Medium");
    const humanReview = Number(data.human_review_probability) || 0;

    sessionAnalytics.total++;
    sessionAnalytics.urgencyTotal += urgency;

    if (priority.toLowerCase() === "high") {
        sessionAnalytics.high++;
    }

    if (humanReview >= 60) {
        sessionAnalytics.escalations++;
    }

    document.getElementById("totalTickets").textContent =
        sessionAnalytics.total;

    document.getElementById("highTickets").textContent =
        sessionAnalytics.high;

    document.getElementById("avgUrgency").textContent =
        (sessionAnalytics.urgencyTotal / sessionAnalytics.total).toFixed(1)
        + "/10";

    document.getElementById("escalations").textContent =
        sessionAnalytics.escalations;

    const mode = data.provider === "Cloudflare Workers AI"
        ? "Live AI"
        : "Demo";

    sessionAnalytics.history.unshift({
        message,
        department: data.department || "General Support",
        priority,
        urgency,
        mode
    });

    // Keep the most recent 10 analyzed tickets.
    sessionAnalytics.history = sessionAnalytics.history.slice(0, 10);

    const table = document.getElementById("ticketHistory");

    table.innerHTML = sessionAnalytics.history.map((ticket) => {
        const priorityClass = ["high", "medium", "low"].includes(
            ticket.priority.toLowerCase()
        ) ? ticket.priority.toLowerCase() : "medium";

        return `
            <tr>
                <td>${escapeHTML(ticket.message)}</td>
                <td>${escapeHTML(ticket.department)}</td>
                <td>
                    <span class="priority-pill priority-${priorityClass}">
                        ${escapeHTML(ticket.priority)}
                    </span>
                </td>
                <td>${ticket.urgency}/10</td>
                <td>${escapeHTML(ticket.mode)}</td>
            </tr>
        `;
    }).join("");
}

// Wrap the original analysis handler so successful results
// also update the dashboard analytics.
const originalAnalyzeButton = document.getElementById("analyzeBtn");
const originalAnalyzeHandler = originalAnalyzeButton.onclick;

// The existing handler uses addEventListener, so listen for
// successful results via a small fetch-response observer instead.
const originalFetch = window.fetch;

window.fetch = async function (...args) {
    const response = await originalFetch.apply(this, args);

    if (
        args[0] === "/api/analyze" &&
        response.ok &&
        args[1]?.method === "POST"
    ) {
        try {
            const data = await response.clone().json();

            if (!data.error) {
                const body = JSON.parse(args[1].body);
                updateAnalytics(data, body.message);
            }
        } catch (error) {
            console.error("Could not update analytics:", error);
        }
    }

    return response;
};




/* Persistent ticket history */

async function loadPersistentHistory() {
    const query = document.getElementById("historySearch").value.trim();
    const table = document.getElementById("ticketHistory");

    table.innerHTML = `
        <tr><td colspan="5" class="history-empty">Loading ticket history...</td></tr>
    `;

    try {
        const response = await fetch(
            `/api/history?q=${encodeURIComponent(query)}&limit=100`
        );

        if (!response.ok) {
            throw new Error("Could not load ticket history.");
        }

        const result = await response.json();
        const tickets = result.tickets || [];

        if (!tickets.length) {
            table.innerHTML = `
                <tr><td colspan="5" class="history-empty">
                    No matching tickets found.
                </td></tr>
            `;
            return;
        }

        table.innerHTML = tickets.map((ticket) => {
            const priority = String(ticket.priority || "Medium");
            const normalized = ["high", "medium", "low"].includes(
                priority.toLowerCase()
            ) ? priority.toLowerCase() : "medium";

            const date = ticket.created_at
                ? new Date(ticket.created_at).toLocaleString()
                : "—";

            return `
                <tr>
                    <td>
                        <strong>#${ticket.id}</strong><br>
                        ${escapeHTML(ticket.message)}<br>
                        <small>${escapeHTML(date)}</small>
                    </td>
                    <td>${escapeHTML(ticket.department)}</td>
                    <td>
                        <span class="priority-pill priority-${normalized}">
                            ${escapeHTML(priority)}
                        </span>
                    </td>
                    <td>${escapeHTML(ticket.urgency_score)}/10</td>
                    <td>${escapeHTML(ticket.provider)}</td>
                </tr>
            `;
        }).join("");
    } catch (error) {
        table.innerHTML = `
            <tr><td colspan="5" class="history-empty">
                Unable to load history. Check that the backend is running.
            </td></tr>
        `;
        console.error(error);
    }
}

document.getElementById("searchHistoryBtn").addEventListener(
    "click",
    loadPersistentHistory
);

document.getElementById("historySearch").addEventListener(
    "keydown",
    (event) => {
        if (event.key === "Enter") {
            loadPersistentHistory();
        }
    }
);

// Load previously saved tickets when the page opens.
loadPersistentHistory();


/* Company data import and business intelligence */

document.getElementById("importBtn").addEventListener("click", async () => {
    const fileInput = document.getElementById("csvFile");
    const status = document.getElementById("importStatus");
    const button = document.getElementById("importBtn");
    const file = fileInput.files[0];

    if (!file) {
        status.textContent = "Please select a CSV file first.";
        return;
    }

    const formData = new FormData();
    formData.append("file", file);

    button.disabled = true;
    button.textContent = "Importing...";
    status.textContent = "Uploading and processing tickets...";

    try {
        const response = await fetch("/api/import-csv", {
            method: "POST",
            body: formData
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.detail || "Import failed.");
        }

        status.textContent =
            `${data.message} Skipped: ${data.skipped}.`;

        await loadPersistentHistory();
        await loadCompanyInsights();
    } catch (error) {
        status.textContent = error.message || "Could not import the file.";
    } finally {
        button.disabled = false;
        button.textContent = "Import tickets";
    }
});

async function loadCompanyInsights() {
    const queue = document.getElementById("priorityQueue");
    const departments = document.getElementById("departmentInsights");

    queue.innerHTML = `
        <tr><td colspan="5" class="history-empty">Loading insights...</td></tr>
    `;

    try {
        const response = await fetch("/api/insights");

        if (!response.ok) {
            throw new Error("Could not retrieve insights.");
        }

        const data = await response.json();
        const summary = data.summary || {};

        // Display database-wide metrics, not just this browser session.
        document.getElementById("totalTickets").textContent =
            summary.total || 0;

        document.getElementById("highTickets").textContent =
            summary.high_priority || 0;

        document.getElementById("avgUrgency").textContent =
            `${Number(summary.average_urgency || 0).toFixed(1)}/10`;

        document.getElementById("escalations").textContent =
            summary.review_needed || 0;

        const items = data.departments || [];

        departments.innerHTML = items.length
            ? items.map((item) => `
                <div class="department-card">
                    <p>${escapeHTML(item.department || "Unassigned")}</p>
                    <strong>${Number(item.ticket_count || 0)}</strong>
                    <small>Tickets received</small>
                </div>
            `).join("")
            : `<p class="muted">No tickets available yet.</p>`;

        const tickets = data.priority_queue || [];

        queue.innerHTML = tickets.length
            ? tickets.map((ticket) => {
                const priority = String(ticket.priority || "Medium");
                const normalized = ["high", "medium", "low"].includes(
                    priority.toLowerCase()
                ) ? priority.toLowerCase() : "medium";

                return `
                    <tr>
                        <td>
                            <strong>#${ticket.id}</strong><br>
                            ${escapeHTML(
                                String(ticket.message || "").slice(0, 180)
                            )}
                        </td>
                        <td>${escapeHTML(ticket.department)}</td>
                        <td>
                            <span class="priority-pill priority-${normalized}">
                                ${escapeHTML(priority)}
                            </span>
                        </td>
                        <td>${Number(ticket.urgency_score || 0)}/10</td>
                        <td>${Number(
                            ticket.human_review_probability || 0
                        )}%</td>
                    </tr>
                `;
            }).join("")
            : `<tr><td colspan="5" class="history-empty">
                No tickets to prioritize.
               </td></tr>`;
    } catch (error) {
        queue.innerHTML = `
            <tr><td colspan="5" class="history-empty">
                ${escapeHTML(error.message)}
            </td></tr>
        `;
    }
}

document.getElementById("refreshInsights").addEventListener(
    "click",
    loadCompanyInsights
);

loadCompanyInsights();
