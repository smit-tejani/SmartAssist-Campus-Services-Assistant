// ======================================================================================
//                              NOTIFICATION SYSTEM
// ======================================================================================

let currentUserEmail = "";

// Fetch and display notifications
async function loadNotifications() {
  try {
    const response = await fetch("/api/notifications?limit=10");
    if (!response.ok) throw new Error("Failed to fetch notifications");

    const notifications = await response.json();
    const notificationList = document.getElementById("notification-list");
    const notificationCount = document.getElementById("notification-count");

    if (notifications.length === 0) {
      notificationList.innerHTML = `
          <div class="notification-empty">
            <i class="fa-solid fa-bell-slash"></i>
            <p>No notifications</p>
          </div>
        `;
      notificationCount.style.display = "none";
      return;
    }

    // Count unread
    const unreadCount = notifications.filter(
      (n) => n.status === "unread"
    ).length;
    if (unreadCount > 0) {
      notificationCount.textContent = unreadCount > 99 ? "99+" : unreadCount;
      notificationCount.style.display = "block";
    } else {
      notificationCount.style.display = "none";
    }

    // Render notifications
    notificationList.innerHTML = notifications
      .map(
        (notif) => `
        <div class="notification-item ${
          notif.status === "unread" ? "unread" : ""
        } ${notif.priority === "high" ? "notification-priority-high" : ""} ${
          notif.priority === "urgent" ? "notification-priority-urgent" : ""
        }"
             onclick="markAsRead('${notif._id}', '${notif.link || "#"}')">
          <div class="notification-title">${notif.title || "Notification"}</div>
          <div class="notification-message">${notif.message || ""}</div>
          <div class="notification-time">${formatNotificationTime(
            notif.created_at
          )}</div>
          <button class="delete-notification" onclick="deleteNotification(event, '${
            notif._id
          }')">
            <i class="fa-solid fa-times"></i>
          </button>
        </div>
      `
      )
      .join("");
  } catch (error) {
    console.error("Error loading notifications:", error);
    document.getElementById("notification-list").innerHTML = `
        <div class="notification-empty">
          <i class="fa-solid fa-exclamation-triangle"></i>
          <p>Error loading notifications</p>
        </div>
      `;
  }
}

// Format notification time
function formatNotificationTime(timestamp) {
  const date = new Date(timestamp);
  const now = new Date();
  const diffMs = now - date;
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMs / 3600000);
  const diffDays = Math.floor(diffMs / 86400000);

  if (diffMins < 1) return "Just now";
  if (diffMins < 60) return `${diffMins} min${diffMins > 1 ? "s" : ""} ago`;
  if (diffHours < 24) return `${diffHours} hour${diffHours > 1 ? "s" : ""} ago`;
  if (diffDays < 7) return `${diffDays} day${diffDays > 1 ? "s" : ""} ago`;
  return date.toLocaleDateString();
}

// Mark notification as read
async function markAsRead(notificationId, link) {
  try {
    await fetch(`/api/notifications/${notificationId}/read`, {
      method: "PUT",
    });

    // Reload notifications
    await loadNotifications();

    // Navigate to link if provided
    if (link && link !== "#") {
      window.location.href = link;
    }
  } catch (error) {
    console.error("Error marking notification as read:", error);
  }
}

// Delete notification
async function deleteNotification(event, notificationId) {
  event.stopPropagation(); // Prevent marking as read

  try {
    await fetch(`/api/notifications/${notificationId}`, {
      method: "DELETE",
    });

    // Reload notifications
    await loadNotifications();
  } catch (error) {
    console.error("Error deleting notification:", error);
  }
}

// Mark all as read
async function markAllAsRead() {
  try {
    const response = await fetch("/api/notifications/mark-all-read", {
      method: "PUT",
    });

    if (!response.ok) throw new Error("Failed to mark all as read");

    const result = await response.json();
    console.log(`Marked ${result.count} notifications as read`);

    // Reload notifications
    await loadNotifications();
  } catch (error) {
    console.error("Error marking all as read:", error);
  }
}

// Show all notifications (placeholder for future page)
function showAllNotifications() {
  alert("View All Notifications feature coming soon!");
  // TODO: Navigate to dedicated notifications page
}

// Toggle notification dropdown
const notificationIcon = document.getElementById("notification-icon");
const notificationDropdown = document.getElementById("notification-dropdown");

notificationIcon.addEventListener("click", (e) => {
  e.stopPropagation();
  notificationDropdown.classList.toggle("show");
  // Close profile dropdown if open
  document.getElementById("profile-dropdown").classList.remove("show");

  // Load notifications when opening
  if (notificationDropdown.classList.contains("show")) {
    loadNotifications();
  }
});

// Close dropdown when clicking outside
document.addEventListener("click", (e) => {
  if (
    !notificationDropdown.contains(e.target) &&
    e.target !== notificationIcon
  ) {
    notificationDropdown.classList.remove("show");
  }
});

// Poll for new notifications every 30 seconds
setInterval(async () => {
  const response = await fetch("/api/notifications/unread/count");
  if (response.ok) {
    const data = await response.json();
    const notificationCount = document.getElementById("notification-count");
    if (data.count > 0) {
      notificationCount.textContent = data.count > 99 ? "99+" : data.count;
      notificationCount.style.display = "block";
    } else {
      notificationCount.style.display = "none";
    }
  }
}, 30000); // 30 seconds

// Initial load
loadNotifications();

// ======================================================================================
//                              EXISTING CODE
// ======================================================================================

// Utility to get status color
function getStatusColor(status) {
  switch (status?.toLowerCase()) {
    case "open":
      return "blue";
    case "in progress":
      return "orange";
    case "closed":
      return "green";
    case "cancelled":
      return "red";
    default:
      return "gray";
  }
}

// Utility to get priority color
function getPriorityColor(priority) {
  switch (priority?.toLowerCase()) {
    case "low":
      return "green";
    case "medium":
      return "orange";
    case "high":
      return "red";
    case "urgent":
      return "darkred";
    default:
      return "gray";
  }
}

// Fetch tickets
async function fetchTickets() {
  ticketsList.innerHTML = "Loading...";
  try {
    // Get current user's email
    const userRes = await fetch("/api/user");
    if (!userRes.ok) throw new Error("Failed to fetch user information");
    const user = await userRes.json();
    const studentEmail = user.email;

    // Fetch tickets for this student only
    const res = await fetch(
      `/api/tickets?status=open&student_email=${encodeURIComponent(
        studentEmail
      )}`
    );
    if (!res.ok) throw new Error(`Error ${res.status}`);
    const data = await res.json();
    renderTickets(data.tickets || []);
  } catch (e) {
    console.error(e);
    ticketsList.innerHTML = "<p>Error loading tickets.</p>";
  }
}

// Cancel ticket function (example AJAX)
async function cancelTicket(ticketId) {
  if (!ticketId) return;
  if (!confirm("Are you sure you want to cancel this ticket?")) return;

  try {
    const res = await fetch(`/api/tickets/cancel/${ticketId}`, {
      method: "POST",
    });
    if (!res.ok) {
      alert("Failed to cancel ticket. Please try again.");
      return;
    }
    const data = await res.json();
    if (data.success) {
      alert("Ticket cancelled successfully!");
      fetchTickets(); // Refresh the list
      updateCounts(); // Refresh count widget
    } else {
      alert(data.message || "Failed to cancel ticket.");
    }
  } catch (e) {
    console.error("Error cancelling ticket:", e);
    alert("Error connecting to server.");
  }
}

// Cancel appointment
async function cancelAppointment(appointmentId) {
  if (!appointmentId) return;
  if (!confirm("Are you sure you want to cancel this appointment?")) return;

  try {
    const res = await fetch(`/api/appointments/cancel/${appointmentId}`, {
      method: "POST",
    });
    if (!res.ok) {
      alert("Failed to cancel appointment. Please try again.");
      return;
    }
    const data = await res.json();
    if (data.success) {
      alert("Appointment cancelled successfully!");
      fetchAppts(); // Refresh the appointments list
      loadStudentAppointmentReminders(); // Refresh appointment reminders
      updateCounts(); // Refresh count widget
    } else {
      alert(data.message || "Failed to cancel appointment.");
    }
  } catch (e) {
    console.error("Error cancelling appointment:", e);
    alert("Error connecting to server.");
  }
}

// Raise Ticket Modal Logic
const modal = document.getElementById("ticket-modal");
const closeBtn = document.getElementById("close-ticket-btn");
const cancelBtn = document.getElementById("cancel-ticket-btn");
const ticketForm = document.getElementById("ticket-form");
const feedback = document.getElementById("ticket-feedback");

const raiseTicketCard = document.getElementById("raise-ticket-card");
const raiseTicketSidebar = document.getElementById("raise-ticket-sidebar");

// Open modal
if (raiseTicketCard) {
  raiseTicketCard.addEventListener("click", (e) => {
    e.preventDefault();
    modal.classList.add("show");
    feedback.classList.add("hidden");
    ticketForm.reset();

    // Reset preferred staff dropdown
    const ticketStaffSelect = document.getElementById("ticket-staff-select");
    if (ticketStaffSelect) {
      ticketStaffSelect.innerHTML =
        '<option value="">Select category first or leave blank</option>';
      ticketStaffSelect.disabled = false; // Re-enable dropdown on reset
    }
  });
}

// Category change event - load staff members for ticket
const ticketCategorySelect = document.getElementById("ticket-category-select");
const ticketStaffSelect = document.getElementById("ticket-staff-select");

// Map categories to departments for staff lookup
const categoryToDepartment = {
  Technical: "IT Support",
  Administrative: "Admin",
  Academic: "Academic Advisors",
  "Financial Aid": "Financial Aid",
  Registrar: "Registrar",
};

if (ticketCategorySelect && ticketStaffSelect) {
  ticketCategorySelect.addEventListener("change", async (e) => {
    const category = e.target.value;

    if (!category || category === "Other") {
      ticketStaffSelect.innerHTML =
        '<option value="">Not applicable or leave blank</option>';
      return;
    }

    const department = categoryToDepartment[category];
    if (!department) {
      ticketStaffSelect.innerHTML = '<option value="">Leave blank</option>';
      return;
    }

    // Special handling for Admin department - auto-assign to admin
    if (department === "Admin") {
      ticketStaffSelect.innerHTML =
        '<option value="auto-assign-admin">Auto-assigned to Admin</option>';
      ticketStaffSelect.disabled = true;
      return;
    }

    // Enable dropdown for other departments
    ticketStaffSelect.disabled = false;

    // Fetch staff members for selected category/department
    ticketStaffSelect.innerHTML =
      '<option value="">Loading staff members...</option>';

    try {
      const response = await fetch(
        `/api/staff/department/${encodeURIComponent(department)}`
      );
      if (!response.ok) throw new Error("Failed to fetch staff");

      const staffMembers = await response.json();

      ticketStaffSelect.innerHTML =
        '<option value="">None (Admin will assign)</option>';
      staffMembers.forEach((staff) => {
        const option = document.createElement("option");
        option.value = staff.email;
        option.textContent = `${staff.full_name || staff.email}`;
        option.setAttribute("data-name", staff.full_name || staff.email);
        ticketStaffSelect.appendChild(option);
      });
    } catch (error) {
      console.error("Error fetching staff:", error);
      ticketStaffSelect.innerHTML =
        '<option value="">Leave blank (Admin will assign)</option>';
    }
  });
}

// Close modal
[closeBtn, cancelBtn].forEach((btn) =>
  btn.addEventListener("click", () => {
    modal.classList.remove("show");
    ticketForm.reset();
    feedback.classList.add("hidden");

    // Reset staff dropdown state
    const ticketStaffSelect = document.getElementById("ticket-staff-select");
    if (ticketStaffSelect) {
      ticketStaffSelect.disabled = false;
      ticketStaffSelect.innerHTML =
        '<option value="">Select category first or leave blank</option>';
    }
  })
);

// Close on outside click
window.addEventListener("click", (e) => {
  if (e.target === modal) {
    modal.classList.remove("show");
    ticketForm.reset();
    feedback.classList.add("hidden");

    // Reset staff dropdown state
    const ticketStaffSelect = document.getElementById("ticket-staff-select");
    if (ticketStaffSelect) {
      ticketStaffSelect.disabled = false;
      ticketStaffSelect.innerHTML =
        '<option value="">Select category first or leave blank</option>';
    }
  }
});

// Submit form (AJAX placeholder)
ticketForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  feedback.classList.remove("hidden");
  feedback.textContent = "Submitting ticket...";

  const formData = new FormData(ticketForm);

  // Handle disabled staff select (for auto-assign-admin)
  const ticketStaffSelect = document.getElementById("ticket-staff-select");
  if (
    ticketStaffSelect &&
    ticketStaffSelect.disabled &&
    ticketStaffSelect.value
  ) {
    formData.set("preferred_staff", ticketStaffSelect.value);
  }

  // Fetch student info and add to form data
  try {
    const userRes = await fetch("/api/user");
    if (userRes.ok) {
      const user = await userRes.json();
      formData.append("student_email", user.email);
      formData.append("student_name", user.full_name || "Unknown");
    }
  } catch (err) {
    console.error("Error fetching user info:", err);
    feedback.textContent = "Error fetching user information.";
    return;
  }

  try {
    const res = await fetch("/raise_ticket", {
      method: "POST",
      body: formData,
    });
    const data = await res.json();
    if (data.success) {
      feedback.textContent = "Ticket submitted successfully!";
      ticketForm.reset();
      // refresh counts and tickets list
      updateCounts();
      loadTickets();
      setTimeout(() => modal.classList.remove("show"), 1500);
    } else {
      feedback.textContent =
        data.error || "Failed to submit ticket. Please try again.";
    }
  } catch (err) {
    feedback.textContent = "Error connecting to server.";
  }
});

// Book Appointment Modal Logic
const appointmentModal = document.getElementById("appointment-modal");
const closeAppointmentBtn = document.getElementById("close-appointment-btn");
const cancelAppointmentBtn = document.getElementById("cancel-appointment-btn");
const appointmentForm = document.getElementById("appointment-form");
const appointmentFeedback = document.getElementById("appointment-feedback");

const bookAppointmentCard = document.getElementById("book-appointment-card");
const bookAppointmentSidebar = document.getElementById(
  "book-appointment-sidebar"
);

// Open modal
if (bookAppointmentCard) {
  bookAppointmentCard.addEventListener("click", (e) => {
    e.preventDefault();
    appointmentModal.classList.add("show");
    appointmentFeedback.classList.add("hidden");
    appointmentForm.reset();

    // Set minimum date to today
    const today = new Date().toISOString().split("T")[0];
    const dateInput = appointmentForm.querySelector('input[name="date"]');
    if (dateInput) {
      dateInput.setAttribute("min", today);
    }

    // Reset staff dropdown
    const staffSelect = document.getElementById("staff-select");
    staffSelect.disabled = true;
    staffSelect.innerHTML =
      '<option value="">First select a department</option>';
  });
}

// Department change event - load staff members
const departmentSelect = document.getElementById("department-select");
const staffSelect = document.getElementById("staff-select");

if (departmentSelect) {
  departmentSelect.addEventListener("change", async (e) => {
    const department = e.target.value;

    if (!department) {
      staffSelect.disabled = true;
      staffSelect.innerHTML =
        '<option value="">First select a department</option>';
      return;
    }

    // Special handling for Admin department - auto-assign to admin
    if (department === "Admin") {
      staffSelect.innerHTML =
        '<option value="auto-assign-admin">Auto-assigned to Admin</option>';
      staffSelect.disabled = true;
      return;
    }

    // Fetch staff members for selected department
    staffSelect.disabled = true;
    staffSelect.innerHTML =
      '<option value="">Loading staff members...</option>';

    try {
      const response = await fetch(
        `/api/staff/department/${encodeURIComponent(department)}`
      );
      if (!response.ok) throw new Error("Failed to fetch staff");

      const staffMembers = await response.json();

      if (staffMembers.length === 0) {
        staffSelect.innerHTML =
          '<option value="">No staff available in this department</option>';
        staffSelect.disabled = true;
      } else {
        staffSelect.innerHTML =
          '<option value="">Select a staff member</option>';
        staffMembers.forEach((staff) => {
          const option = document.createElement("option");
          option.value = staff.email;
          option.textContent = `${staff.full_name || staff.email} - ${
            staff.department || ""
          }`;
          option.setAttribute("data-name", staff.full_name || staff.email);
          staffSelect.appendChild(option);
        });
        staffSelect.disabled = false;
      }
    } catch (error) {
      console.error("Error fetching staff:", error);
      staffSelect.innerHTML =
        '<option value="">Error loading staff members</option>';
      staffSelect.disabled = true;
    }
  });
}

// Close modal
[closeAppointmentBtn, cancelAppointmentBtn].forEach((btn) =>
  btn.addEventListener("click", () => {
    appointmentModal.classList.remove("show");
    appointmentForm.reset();
    appointmentFeedback.classList.add("hidden");

    // Reset staff dropdown state
    const staffSelect = document.getElementById("staff-select");
    if (staffSelect) {
      staffSelect.disabled = true;
      staffSelect.innerHTML =
        '<option value="">First select a department</option>';
    }
  })
);

// Close on outside click
window.addEventListener("click", (e) => {
  if (e.target === appointmentModal) {
    appointmentModal.classList.remove("show");
    appointmentForm.reset();
    appointmentFeedback.classList.add("hidden");

    // Reset staff dropdown state
    const staffSelect = document.getElementById("staff-select");
    if (staffSelect) {
      staffSelect.disabled = true;
      staffSelect.innerHTML =
        '<option value="">First select a department</option>';
    }
  }
});

// Submit form (AJAX placeholder)
appointmentForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  appointmentFeedback.classList.remove("hidden");
  appointmentFeedback.textContent = "Booking appointment...";

  const formData = new FormData(appointmentForm);

  // Handle disabled staff select (for auto-assign-admin)
  const staffSelect = document.getElementById("staff-select");
  if (staffSelect && staffSelect.disabled && staffSelect.value) {
    formData.set("assigned_staff", staffSelect.value);
  }

  // Fetch student info and add to form data
  try {
    const userRes = await fetch("/api/user");
    if (userRes.ok) {
      const user = await userRes.json();
      formData.append("student_email", user.email);
      formData.append("student_name", user.full_name || "Unknown");
    }
  } catch (err) {
    console.error("Error fetching user info:", err);
    appointmentFeedback.textContent = "Error fetching user information.";
    return;
  }

  try {
    const res = await fetch("/book_appointment", {
      method: "POST",
      body: formData,
    });
    const data = await res.json();
    if (data.success) {
      appointmentFeedback.textContent = "Appointment booked successfully!";
      appointmentForm.reset();
      updateCounts();
      setTimeout(() => appointmentModal.classList.remove("show"), 1500);
    } else {
      appointmentFeedback.textContent =
        data.error || "Failed to book appointment. Please try again.";
    }
  } catch (err) {
    appointmentFeedback.textContent = "Error connecting to server.";
  }
});

// New: Open Tickets & Upcoming Appointments modals
const ticketsModal = document.getElementById("tickets-modal");
const apptsModal = document.getElementById("appts-modal");
const ticketsList = document.getElementById("tickets-list");
const apptsList = document.getElementById("appts-list");

const openTicketsById = document.getElementById("open-tickets-widget");
const upcomingById = document.getElementById("upcoming-appts-widget");

function renderTickets(items) {
  if (!items || items.length === 0) {
    ticketsList.innerHTML = "<p>No open tickets.</p>";
    return;
  }
  ticketsList.innerHTML = items
    .map((t) => {
      const assignedStaff =
        t.assigned_to_name || t.assigned_staff || "Not Assigned Yet";
      const preferredStaff = t.preferred_staff_name || t.preferred_staff;

      return `
    <div class="ticket-card card">
      <div class="ticket-header">
        <strong>Subject:</strong> ${t.subject}
        <span class="ticket-status" style="color: ${getStatusColor(
          t.status
        )};">${t.status}</span>
      </div>
      <div class="ticket-meta">
        <div>Category: ${
          t.category
        } • Priority: <span style="color: ${getPriorityColor(t.priority)};">${
        t.priority
      }</span></div>
        <div>Created: ${
          t.date_created || t.created_at || "Unknown"
        } • Last Updated: ${t.last_updated}</div>
        <div><strong>Assigned Staff:</strong> ${assignedStaff}</div>
        ${
          preferredStaff
            ? `<div style="color: #718096;"><strong>Preferred Staff:</strong> ${preferredStaff}</div>`
            : ""
        }
      </div>
      <div class="ticket-description">
        <strong>Description:</strong> ${t.description}
      </div>
      <div class="ticket-attachment"><strong>Attachment:</strong> ${
        t.attachment_id
          ? `<a href="/api/attachment/${t.attachment_id}">Download</a>`
          : "None"
      }</div>
      <div class="ticket-actions">
        <button class="cancel-ticket-btn" onclick="cancelTicket('${
          t._id
        }')">Cancel Ticket</button>
      </div>
    </div>
  `;
    })
    .join("");
}

function renderAppts(items) {
  if (!items || items.length === 0) {
    apptsList.innerHTML = "<p>No upcoming appointments.</p>";
    return;
  }
  apptsList.innerHTML = items
    .map((a) => {
      const assignedStaff =
        a.assigned_staff_name || a.assigned_staff || "Not Assigned Yet";
      const statusColor =
        a.status === "Confirmed"
          ? "green"
          : a.status === "Pending"
          ? "orange"
          : a.status === "Cancelled"
          ? "red"
          : "gray";

      return `
    <div class="appt-card card">
      <div class="appt-header">
        <strong>Subject:</strong> ${a.subject || "N/A"}
        <span class="appt-status" style="color: ${statusColor};">${a.status} ${
        a.countdown || ""
      }</span>
      </div>
      <div class="appt-meta">
        <div><strong>Department:</strong> ${a.department || "N/A"}</div>
        <div><strong>Assigned Staff:</strong> ${assignedStaff}</div>
        <div><strong>Date & Time:</strong> ${a.date} • ${
        a.time_slot || a.time || "N/A"
      }</div>
        <div><strong>Meeting Mode:</strong> ${a.meeting_mode || "N/A"}</div>
        <div><strong>Location:</strong> ${
          a.location_mode || "To be assigned"
        }</div>
      </div>
      <div class="appt-notes"><strong>Notes:</strong> ${a.notes || "None"}</div>
      <div class="appt-actions">
        <button class="cancel-appt-btn" onclick="cancelAppointment('${
          a._id
        }')">Cancel</button>
        ${
          a.status === "Confirmed"
            ? `<button class="reschedule-appt-btn" onclick="rescheduleAppointment('${a._id}')">Reschedule</button>`
            : ""
        }
      </div>
    </div>
  `;
    })
    .join("");
}

async function fetchTickets() {
  ticketsList.innerHTML = "Loading...";
  try {
    // Get current user's email
    const userRes = await fetch("/api/user");
    if (!userRes.ok) {
      console.error("Failed to fetch user information");
      ticketsList.innerHTML = "<p>Error loading user information.</p>";
      return;
    }
    const user = await userRes.json();
    const studentEmail = user.email;

    // Fetch tickets for this student only (exclude Resolved and Cancelled)
    const res = await fetch(
      `/api/tickets?status=open&student_email=${encodeURIComponent(
        studentEmail
      )}`
    );
    if (!res.ok) {
      console.error("Failed to fetch tickets:", res.status, res.statusText);
      ticketsList.innerHTML =
        "<p>Error loading tickets. Please try again later.</p>";
      return;
    }
    const data = await res.json();
    console.log("Tickets API response:", data);

    // Extra client-side filtering to ensure no Resolved/Cancelled tickets show
    const activeTickets = (data || []).filter(
      // <-- FIX
      (ticket) =>
        ticket.status !== "Resolved" &&
        ticket.status !== "Cancelled" &&
        ticket.status !== "resolved" &&
        ticket.status !== "cancelled"
    );

    renderTickets(activeTickets);
  } catch (e) {
    console.error("Error in fetchTickets:", e);
    ticketsList.innerHTML =
      "<p>Error loading tickets. Please check your connection.</p>";
  }
}

async function fetchAppts() {
  apptsList.innerHTML = "Loading...";
  try {
    // Get current user's email
    const userRes = await fetch("/api/user");
    if (!userRes.ok) {
      console.error("Failed to fetch user information");
      apptsList.innerHTML = "<p>Error loading user information.</p>";
      return;
    }
    const user = await userRes.json();
    const studentEmail = user.email;

    // Fetch appointments for this student only (exclude Cancelled)
    const res = await fetch(
      `/api/appointments?upcoming=true&student_email=${encodeURIComponent(
        studentEmail
      )}`
    );
    const data = await res.json();

    // Extra client-side filtering to ensure no Cancelled appointments show
    const activeAppointments = (data || []).filter(
      // <-- FIX
      (appointment) =>
        appointment.status !== "Cancelled" && appointment.status !== "cancelled"
    );

    renderAppts(activeAppointments);
  } catch (e) {
    console.error("Error in fetchAppts:", e);
    apptsList.innerHTML = "<p>Error loading appointments.</p>";
  }
}

// Load student appointment reminders (shows next upcoming appointment)
async function loadStudentAppointmentReminders() {
  try {
    // Get current user's email
    const userRes = await fetch("/api/user");
    if (!userRes.ok) return;
    const user = await userRes.json();
    const studentEmail = user.email;

    const res = await fetch(
      `/api/appointments?student_email=${encodeURIComponent(studentEmail)}`
    );
    if (!res.ok) return;

    const data = await res.json();
    const appointments = data || []; // <-- FIX

    const reminderContainer = document.getElementById(
      "student-appointment-reminders"
    );
    reminderContainer.innerHTML = "";

    // Get today's date
    const today = new Date();
    today.setHours(0, 0, 0, 0);

    // Filter confirmed appointments with date >= today (exclude Cancelled)
    const upcomingAppointments = appointments.filter((app) => {
      if (app.status !== "Confirmed") return false;
      if (app.status === "Cancelled" || app.status === "cancelled")
        return false;
      const appDate = new Date(app.date);
      appDate.setHours(0, 0, 0, 0);
      return appDate >= today;
    });

    // Sort by date ascending
    upcomingAppointments.sort((a, b) => new Date(a.date) - new Date(b.date));

    if (upcomingAppointments.length === 0) {
      // No upcoming appointments - show a friendly message
      reminderContainer.innerHTML = `
        <div style="background:linear-gradient(135deg, #f7fafc 0%, #edf2f7 100%); padding:1rem 1.5rem; border-radius:12px; border-left:4px solid #cbd5e0; display:flex; align-items:center; gap:1rem;">
          <i class="fa-solid fa-calendar-check" style="font-size:1.5rem; color:#a0aec0;"></i>
          <div style="color:#718096; font-size:0.95rem;">
            No upcoming appointments scheduled
          </div>
        </div>
      `;
      return;
    }

    // Show reminders for upcoming appointments (max 3)
    const remindersToShow = upcomingAppointments.slice(0, 3);

    remindersToShow.forEach((app) => {
      const appDate = new Date(app.date);
      appDate.setHours(0, 0, 0, 0);

      // Calculate days difference
      const diffTime = appDate - today;
      const diffDays = Math.ceil(diffTime / (1000 * 60 * 60 * 24));

      // Determine message and styling based on days left
      let message, bgGradient, borderColor, iconColor, icon;

      if (diffDays === 0) {
        message = `Your appointment is <strong>today</strong>!`;
        bgGradient = "linear-gradient(135deg, #fff5f5 0%, #fed7d7 100%)";
        borderColor = "#fc8181";
        iconColor = "#e53e3e";
        icon = "fa-bell";
      } else if (diffDays === 1) {
        message = `Your appointment is <strong>tomorrow</strong>!`;
        bgGradient = "linear-gradient(135deg, #fffaf0 0%, #feebc8 100%)";
        borderColor = "#f6ad55";
        iconColor = "#dd6b20";
        icon = "fa-clock";
      } else if (diffDays <= 7) {
        message = `Appointment in <strong>${diffDays} days</strong>`;
        bgGradient = "linear-gradient(135deg, #e6fffa 0%, #b2f5ea 100%)";
        borderColor = "#4fd1c5";
        iconColor = "#319795";
        icon = "fa-calendar-day";
      } else {
        message = `Appointment in <strong>${diffDays} days</strong>`;
        bgGradient = "linear-gradient(135deg, #ebf8ff 0%, #bee3f8 100%)";
        borderColor = "#63b3ed";
        iconColor = "#3182ce";
        icon = "fa-calendar";
      }

      // Get meeting mode display
      const meetingMode =
        app.meeting_mode === "In-Person" ? "in person" : "virtual";
      const meetingIcon =
        app.meeting_mode === "In-Person" ? "fa-user-group" : "fa-video";

      // Format time
      const timeSlot = app.time_slot || "Time TBD";

      const reminderCard = document.createElement("div");
      reminderCard.style.cssText = `
        background:${bgGradient};
        padding:1rem 1.5rem;
        border-radius:12px;
        border-left:4px solid ${borderColor};
        display:flex;
        align-items:center;
        gap:1rem;
        margin-bottom:0.75rem;
        box-shadow:0 2px 8px rgba(0,0,0,0.08);
        transition:all 0.2s;
      `;

      reminderCard.onmouseover = () => {
        reminderCard.style.transform = "translateY(-2px)";
        reminderCard.style.boxShadow = "0 4px 12px rgba(0,0,0,0.12)";
      };

      reminderCard.onmouseout = () => {
        reminderCard.style.transform = "translateY(0)";
        reminderCard.style.boxShadow = "0 2px 8px rgba(0,0,0,0.08)";
      };

      reminderCard.innerHTML = `
        <i class="fa-solid ${icon}" style="font-size:1.8rem; color:${iconColor};"></i>
        <div style="flex:1;">
          <div style="color:#2d3748; font-size:1rem; font-weight:600; margin-bottom:0.25rem;">
            <i class="fa-solid fa-calendar-check" style="color:${iconColor}; font-size:0.9rem;"></i>
            ${message}
          </div>
          <div style="color:#4a5568; font-size:0.875rem; display:flex; gap:1rem; flex-wrap:wrap;">
            <span><i class="fa-solid ${meetingIcon}" style="color:${iconColor};"></i> ${meetingMode}</span>
            <span><i class="fa-solid fa-clock" style="color:${iconColor};"></i> ${timeSlot}</span>
            <span><i class="fa-solid fa-building" style="color:${iconColor};"></i> ${
        app.department
      }</span>
          </div>
        </div>
        <div style="text-align:right;">
          <div style="background:white; color:${iconColor}; border:2px solid ${borderColor}; padding:0.5rem 1rem; border-radius:8px; font-weight:600; font-size:0.875rem; margin-bottom:0.5rem;">
            ${app.subject}
          </div>
          ${
            app.location_mode
              ? `<div style="color:#4a5568; font-size:0.75rem;"><i class="fa-solid fa-location-dot"></i> ${app.location_mode}</div>`
              : ""
          }
        </div>
      `;

      reminderContainer.appendChild(reminderCard);
    });
  } catch (err) {
    console.error("Error loading student appointment reminders:", err);
  }
}

// Load and display events for students
async function loadStudentEvents() {
  try {
    const response = await fetch("/api/events?status=active");
    if (!response.ok) throw new Error("Failed to fetch events");

    const events = await response.json();
    const eventsContainer = document.getElementById("student-events-section");

    // Filter events for students or all
    const studentEvents = events.filter(
      (event) =>
        event.target_audience === "students" || event.target_audience === "all"
    );

    if (studentEvents.length === 0) {
      eventsContainer.innerHTML = "";
      return;
    }

    eventsContainer.innerHTML = "";

    studentEvents.forEach((event) => {
      const eventCard = document.createElement("div");
      eventCard.style.cssText = `
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        border-radius: 10px;
        padding: 1.2rem;
        color: white;
        margin-bottom: 1rem;
        box-shadow: 0 4px 6px rgba(0,0,0,0.1);
      `;

      const priorityIcon =
        event.priority === "high"
          ? "🔴"
          : event.priority === "urgent"
          ? "⚠️"
          : "📢";
      const eventDate = new Date(event.event_date).toLocaleDateString();

      eventCard.innerHTML = `
        <div style="display: flex; justify-content: space-between; align-items: start;">
          <div style="flex: 1;">
            <h3 style="margin: 0 0 0.5rem 0; font-size: 1.1rem;">
              ${priorityIcon} ${event.title}
            </h3>
            <p style="margin: 0 0 0.5rem 0; opacity: 0.9; font-size: 0.95rem;">
              ${event.description}
            </p>
            <div style="display: flex; gap: 1rem; font-size: 0.9rem; opacity: 0.8;">
              <span>📅 ${eventDate}</span>
              <span>⏰ ${event.event_time}</span>
              <span>📂 ${event.category || "General"}</span>
            </div>
          </div>
          ${
            event.status === "completed"
              ? '<span style="background: rgba(255,255,255,0.3); padding: 0.3rem 0.8rem; border-radius: 20px; font-size: 0.85rem;">✓ Completed</span>'
              : ""
          }
        </div>
      `;

      eventsContainer.appendChild(eventCard);
    });
  } catch (err) {
    console.error("Error loading student events:", err);
  }
}

// Update counts in stats widgets
async function updateCounts() {
  try {
    // Get current user's email
    const userRes = await fetch("/api/user");
    if (!userRes.ok) throw new Error("Failed to fetch user information");
    const user = await userRes.json();
    const studentEmail = user.email;

    const t = await fetch(
      `/api/tickets?status=open&student_email=${encodeURIComponent(
        studentEmail
      )}`
    );
    const tdata = await t.json();
    document.getElementById("open-tickets-count").textContent =
      tdata.length || 0;

    // Fetch appointment count for this student only
    const a = await fetch(
      `/api/appointments?upcoming=true&student_email=${encodeURIComponent(
        studentEmail
      )}`
    );
    const adata = await a.json();
    document.getElementById("upcoming-appts-count").textContent =
      adata.length || 0;
  } catch (e) {
    console.error("Failed to update counts", e);
  }
}

// initial counts
updateCounts();
loadStudentEvents(); // Load events on page load
loadStudentAppointmentReminders(); // Load appointment reminders on page load

// wire up openers
if (openTicketsById) {
  openTicketsById.addEventListener("click", () => {
    ticketsModal.classList.add("show");
    fetchTickets();
  });
}
if (upcomingById) {
  upcomingById.addEventListener("click", () => {
    apptsModal.classList.add("show");
    fetchAppts();
  });
}

// close buttons
const closeTicketsBtn = document.getElementById("close-tickets-modal");
const closeApptsBtn = document.getElementById("close-appts-modal");
if (closeTicketsBtn && typeof ticketsModal !== "undefined") {
  closeTicketsBtn.addEventListener("click", () =>
    ticketsModal.classList.remove("show")
  );
}
if (closeApptsBtn && typeof apptsModal !== "undefined") {
  closeApptsBtn.addEventListener("click", () =>
    apptsModal.classList.remove("show")
  );
}

// close on outside click
window.addEventListener("click", (e) => {
  if (e.target === ticketsModal) ticketsModal.classList.remove("show");
  if (e.target === apptsModal) apptsModal.classList.remove("show");
});

// Profile dropdown toggle
const profileIcon = document.getElementById("profile-icon");
const profileDropdown = document.getElementById("profile-dropdown");
profileIcon.onclick = () => {
  profileDropdown.classList.toggle("show");
};

// Close dropdown on outside click
window.addEventListener("click", (e) => {
  if (!profileDropdown.contains(e.target) && e.target !== profileIcon) {
    profileDropdown.classList.remove("show");
  }
});

// Load Tickets function
async function loadTickets() {
  try {
    // First get the current user's email
    const userRes = await fetch("/api/user");
    if (!userRes.ok) {
      throw new Error("Failed to fetch user information");
    }
    const user = await userRes.json();
    const studentEmail = user.email;

    // Fetch tickets for this student only
    const response = await fetch(
      `/api/tickets?status=open&student_email=${encodeURIComponent(
        studentEmail
      )}`
    );
    const data = await response.json();
    const ticketList = document.getElementById("ticket-list");
    ticketList.innerHTML = "";

    if (!data || data.length === 0) {
      // <-- FIX
      ticketList.innerHTML =
        '<tr><td colspan="6" style="text-align: center; color: #718096;">No open tickets</td></tr>';
      return;
    }

    data.forEach((ticket) => {
      // <-- FIX
      const row = document.createElement("tr");
      row.innerHTML = `
        <td>${ticket._id}</td>
        <td>${ticket.subject}</td>
        <td>${ticket.category}</td>
        <td>${ticket.priority}</td>
        <td>${ticket.status}</td>
        <td>${ticket.assigned_to_name || "Not Assigned Yet"}</td>
      `;
      ticketList.appendChild(row);
    });
  } catch (error) {
    console.error("Error loading tickets:", error);
    const ticketList = document.getElementById("ticket-list");
    ticketList.innerHTML =
      '<tr><td colspan="6" style="text-align: center; color: #e53e3e;">Error loading tickets</td></tr>';
  }
}

// Load Appointments function
async function loadAppointments() {
  try {
    // First get the current user's email
    const userRes = await fetch("/api/user");
    if (!userRes.ok) {
      throw new Error("Failed to fetch user information");
    }
    const user = await userRes.json();
    const studentEmail = user.email;

    // Fetch appointments for this student only
    const response = await fetch(
      `/api/appointments?student_email=${encodeURIComponent(studentEmail)}`
    );
    const data = await response.json();
    const appointmentList = document.getElementById("appointment-list");
    appointmentList.innerHTML = "";

    if (!data || data.length === 0) {
      // <-- FIX
      appointmentList.innerHTML =
        '<tr><td colspan="8" style="text-align: center; color: #718096;">No appointments scheduled</td></tr>';
      return;
    }

    data.forEach((appt) => {
      // <-- FIX
      const row = document.createElement("tr");
      const statusColor =
        appt.status === "Confirmed"
          ? "green"
          : appt.status === "Pending"
          ? "orange"
          : appt.status === "Cancelled"
          ? "red"
          : "gray";
      row.innerHTML = `
        <td>${appt.subject || "N/A"}</td>
        <td>${appt.department || "N/A"}</td>
        <td>${
          appt.assigned_staff_name || appt.assigned_staff || "Not Assigned Yet"
        }</td>
        <td>${appt.date}</td>
        <td>${appt.time_slot || appt.time || "N/A"}</td>
        <td>${appt.meeting_mode || "N/A"}</td>
        <td style="color: ${statusColor}; font-weight: 600;">${appt.status}</td>
        <td>${appt.location_mode || "To be assigned"}</td>
      `;
      appointmentList.appendChild(row);
    });
  } catch (error) {
    console.error("Error loading appointments:", error);
    const appointmentList = document.getElementById("appointment-list");
    appointmentList.innerHTML =
      '<tr><td colspan="8" style="text-align: center; color: #e53e3e;">Error loading appointments</td></tr>';
  }
}

document.addEventListener("DOMContentLoaded", async () => {
  try {
    const res = await fetch("/api/user");
    if (res.ok) {
      const user = await res.json();
      const firstName = user.full_name
        ? user.full_name.split(" ")[0]
        : "Student";
      document.querySelector(
        ".hero h2"
      ).textContent = `Welcome, ${firstName} 👋`;
    } else {
      console.error("Failed to fetch user details:", res.statusText);
    }
  } catch (err) {
    console.error("Error fetching user details:", err);
  }
});

document.addEventListener("DOMContentLoaded", async () => {
  try {
    const res = await fetch("/api/user"); // Fetch user details from backend
    if (res.ok) {
      const user = await res.json();
      const firstName = user.full_name ? user.full_name.split(" ")[0] : "User"; // Extract first name or default to 'User'

      // Update the welcome message
      const welcomeMessage = document.querySelector("#welcome-message");
      if (welcomeMessage) {
        welcomeMessage.textContent = `Welcome, ${firstName}`;
      }
    } else {
      console.error("Failed to fetch user details:", res.statusText);
    }
  } catch (err) {
    console.error("Error fetching user details:", err);
  }
});

// Tab switching function
function showTab(tabName) {
  // Update tab buttons
  document
    .querySelectorAll(".tab")
    .forEach((tab) => tab.classList.remove("active"));
  event.target.closest(".tab").classList.add("active");

  // Update tab content
  document.getElementById("ticketsTab").classList.remove("active");
  document.getElementById("appointmentsTab").classList.remove("active");

  if (tabName === "tickets") {
    document.getElementById("ticketsTab").classList.add("active");
  } else {
    document.getElementById("appointmentsTab").classList.add("active");
  }
}

// Load tickets on window load
window.onload = () => {
  loadTickets();
  loadAppointments();
  loadFeedbackCount();
};

document.getElementById("fetch-courses").addEventListener("click", async () => {
  const term = document.getElementById("term").value;
  if (!term) {
    alert("Please select a term");
    return;
  }

  const studentEmail = await fetch("/api/user")
    .then((response) => response.json())
    .then((data) => data.email); // Dynamically fetch email from /api/user endpoint

  // Fetch courses for the selected term
  const coursesResponse = await fetch(`/api/courses/${term}`);
  const courses = await coursesResponse.json();

  // Fetch registered courses for the student
  const registeredResponse = await fetch(
    `/api/registered_courses/${studentEmail}`
  );
  const registeredCourses = await registeredResponse.json();
  const registeredCourseIds = registeredCourses.map((reg) => reg.course_id);

  const coursesTable = document.getElementById("courses-table");
  const coursesBody = document.getElementById("courses-body");

  coursesBody.innerHTML = "";
  courses.forEach((course) => {
    const isRegistered = registeredCourseIds.includes(course._id);
    const row = document.createElement("tr");
    row.innerHTML = `
        <td>${course.title}</td>
        <td>${course.details}</td>
        <td>${course.hours}</td>
        <td>${course.crn}</td>
        <td>${course.schedule_type}</td>
        <td>${course.grade_mode}</td>
        <td>${course.level}</td>
        <td>${course.part_of_term}</td>
        <td>
          <button class="btn" ${isRegistered ? "disabled" : ""} 
            style="${
              isRegistered
                ? "background-color: transparent; border: none; color: gray; cursor: default;"
                : ""
            }"
            onclick="${
              isRegistered ? "" : `registerCourse('${course._id}', '${term}')`
            }">
            ${isRegistered ? "Registered" : "Register"}
          </button>
        </td>
      `;
    coursesBody.appendChild(row);
  });

  coursesTable.style.display = "table";
});

async function registerCourse(courseId, term) {
  const studentEmail = await fetch("/api/user")
    .then((response) => response.json())
    .then((data) => data.email); // Dynamically fetch email from /api/user endpoint

  const response = await fetch("/api/register_course", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      student_email: studentEmail,
      course_id: courseId,
      term,
    }),
  });

  const result = await response.json();
  alert(result.message);

  // Instantly refresh the courses table to reflect registration without page reload
  if (response.ok) {
    document.getElementById("fetch-courses").click();
  }
}

// ================= SURVEYS FUNCTIONALITY =================
let currentSurveyId = null;
let currentQuestionIndex = 0;
let surveyQuestions = [];
let surveyAnswers = {};

// Open surveys modal and load available surveys
async function openSurveysModal() {
  const modal = document.getElementById("surveys-modal");
  modal.classList.add("show");
  await loadAvailableSurveys();
}

// Close surveys modal
function closeSurveysModal() {
  const modal = document.getElementById("surveys-modal");
  modal.classList.remove("show");
}

// Close take survey modal
function closeTakeSurveyModal() {
  const modal = document.getElementById("take-survey-modal");
  modal.classList.remove("show");
  currentSurveyId = null;
  currentQuestionIndex = 0;
  surveyAnswers = {};
}

// Load feedback submitted count
async function loadFeedbackCount() {
  try {
    const response = await fetch("/api/surveys/submitted/count");
    if (!response.ok) throw new Error("Failed to fetch feedback count");

    const data = await response.json();
    document.getElementById("feedback-submitted-count").textContent =
      data.count;
  } catch (error) {
    console.error("Error loading feedback count:", error);
    document.getElementById("feedback-submitted-count").textContent = "0";
  }
}

// Load available surveys
async function loadAvailableSurveys() {
  try {
    const response = await fetch("/api/surveys/available");
    if (!response.ok) throw new Error("Failed to fetch surveys");

    const surveys = await response.json();
    const surveysList = document.getElementById("surveys-list");

    if (surveys.length === 0) {
      surveysList.innerHTML = `
          <div style="text-align:center; padding:3rem; color:#718096;">
            <i class="fa-solid fa-inbox" style="font-size:3rem; opacity:0.3;"></i>
            <h3 style="margin-top:1rem; color:#4a5568;">No Surveys Available</h3>
            <p>Check back later for new surveys!</p>
          </div>
        `;
      return;
    }

    surveysList.innerHTML = surveys
      .map((survey) => {
        const endDate = new Date(survey.end_date).toLocaleDateString();
        const questionCount = survey.questions?.length || 0;
        const estimatedTime = Math.max(1, Math.ceil(questionCount * 0.5)); // 30 sec per question

        return `
          <div style="border:2px solid #e2e8f0; border-radius:10px; padding:1.5rem; background:white; transition:all 0.2s; cursor:pointer;" 
               onmouseover="this.style.borderColor='#0067A5'; this.style.boxShadow='0 4px 12px rgba(0,103,165,0.1)'" 
               onmouseout="this.style.borderColor='#e2e8f0'; this.style.boxShadow='none'">
            <div style="display:flex; justify-content:space-between; align-items:start; margin-bottom:1rem;">
              <div style="flex:1;">
                <h3 style="color:#2d3748; margin:0 0 0.5rem 0; font-size:1.1rem;">
                  ${
                    survey.survey_type === "course_evaluation"
                      ? "🎓"
                      : survey.survey_type === "service_feedback"
                      ? "⭐"
                      : "📋"
                  } 
                  ${survey.title}
                </h3>
                <p style="color:#718096; margin:0; font-size:0.9rem;">${
                  survey.description || "Please share your feedback"
                }</p>
              </div>
              ${
                survey.already_responded
                  ? '<span style="background:#48bb78; color:white; padding:0.3rem 0.8rem; border-radius:20px; font-size:0.8rem; font-weight:600; white-space:nowrap;">✓ Completed</span>'
                  : '<span style="background:#0067A5; color:white; padding:0.3rem 0.8rem; border-radius:20px; font-size:0.8rem; font-weight:600; white-space:nowrap;">Available</span>'
              }
            </div>
            <div style="display:flex; gap:1.5rem; color:#718096; font-size:0.85rem; margin-bottom:1rem;">
              <span><i class="fa-solid fa-list-check"></i> ${questionCount} questions</span>
              <span><i class="fa-solid fa-clock"></i> ~${estimatedTime} min</span>
              <span><i class="fa-solid fa-calendar"></i> Closes ${endDate}</span>
              ${
                survey.is_anonymous
                  ? '<span><i class="fa-solid fa-user-secret"></i> Anonymous</span>'
                  : ""
              }
            </div>
            ${
              !survey.already_responded
                ? `<button onclick="startSurvey('${survey._id}')" style="background:linear-gradient(135deg, #0067A5 0%, #00A99D 100%); color:white; padding:0.6rem 1.2rem; border:none; border-radius:6px; cursor:pointer; font-weight:500; font-size:0.9rem; transition:transform 0.2s;" onmouseover="this.style.transform='translateY(-2px)'" onmouseout="this.style.transform='translateY(0)'">
                Start Survey <i class="fa-solid fa-arrow-right"></i>
              </button>`
                : '<p style="color:#48bb78; font-size:0.9rem; margin:0;"><i class="fa-solid fa-check-circle"></i> Thank you for your feedback!</p>'
            }
          </div>
        `;
      })
      .join("");
  } catch (error) {
    console.error("Error loading surveys:", error);
    document.getElementById("surveys-list").innerHTML = `
        <div style="text-align:center; padding:2rem; color:#e53e3e;">
          <i class="fa-solid fa-exclamation-triangle"></i>
          <p>Error loading surveys. Please try again later.</p>
        </div>
      `;
  }
}

// Start taking a survey
async function startSurvey(surveyId) {
  try {
    const response = await fetch(`/api/surveys/${surveyId}`);
    if (!response.ok) throw new Error("Failed to fetch survey");

    const survey = await response.json();
    currentSurveyId = surveyId;
    surveyQuestions = survey.questions || [];
    currentQuestionIndex = 0;
    surveyAnswers = {};

    document.getElementById("survey-title").textContent = survey.title;
    document.getElementById("survey-description").textContent =
      survey.description || "";

    closeSurveysModal();
    const takeSurveyModal = document.getElementById("take-survey-modal");
    takeSurveyModal.classList.add("show");
    showQuestion(0);
  } catch (error) {
    console.error("Error starting survey:", error);
    alert("Error loading survey. Please try again.");
  }
}

// Show specific question
function showQuestion(index) {
  if (index < 0 || index >= surveyQuestions.length) return;

  currentQuestionIndex = index;
  const question = surveyQuestions[index];
  const progress = ((index + 1) / surveyQuestions.length) * 100;

  document.getElementById("progress-bar").style.width = `${progress}%`;
  document.getElementById("progress-text").textContent = `Question ${
    index + 1
  } of ${surveyQuestions.length}`;

  const questionsContainer = document.getElementById("survey-questions");
  questionsContainer.innerHTML = renderQuestion(question, index);

  // Show/hide navigation buttons
  document.getElementById("prev-question-btn").style.display =
    index > 0 ? "block" : "none";
  document.getElementById("next-question-btn").style.display =
    index < surveyQuestions.length - 1 ? "block" : "none";
  document.getElementById("submit-survey-btn").style.display =
    index === surveyQuestions.length - 1 ? "block" : "none";

  // Restore previous answer if exists
  if (surveyAnswers[question.question_id]) {
    restoreAnswer(question, surveyAnswers[question.question_id]);
  }
}

// Render question based on type
function renderQuestion(question, index) {
  let html = `
      <div style="margin-bottom:2rem;">
        <h3 style="color:#2d3748; margin-bottom:0.5rem; font-size:1.1rem;">
          ${index + 1}. ${question.question_text}
          ${question.required ? '<span style="color:#e53e3e;">*</span>' : ""}
        </h3>
        <div style="margin-top:1rem;">
    `;

  switch (question.question_type) {
    case "rating":
      html += `
          <div style="display:flex; gap:0.5rem; flex-wrap:wrap;">
            ${[1, 2, 3, 4, 5]
              .map(
                (rating) => `
              <label style="cursor:pointer; display:flex; flex-direction:column; align-items:center; padding:1rem; border:2px solid #e2e8f0; border-radius:8px; transition:all 0.2s; min-width:60px;" 
                     onmouseover="this.style.borderColor='#0067A5'; this.style.background='#f7fafc'" 
                     onmouseout="if(!this.querySelector('input').checked) { this.style.borderColor='#e2e8f0'; this.style.background='white' }">
                <input type="radio" name="question_${
                  question.question_id
                }" value="${rating}" ${question.required ? "required" : ""} 
                       onchange="saveAnswer('${
                         question.question_id
                       }', this.value); this.parentElement.parentElement.querySelectorAll('label').forEach(l => { l.style.borderColor='#e2e8f0'; l.style.background='white' }); this.parentElement.style.borderColor='#0067A5'; this.parentElement.style.background='#ebf8ff'"
                       style="display:none;">
                <span style="font-size:1.5rem;">${"⭐".repeat(rating)}</span>
                <span style="font-size:0.85rem; color:#718096; margin-top:0.3rem;">${rating}</span>
              </label>
            `
              )
              .join("")}
          </div>
        `;
      break;

    case "multiple_choice":
      html += `
          <div style="display:flex; flex-direction:column; gap:0.75rem;">
            ${(question.options || [])
              .map(
                (option, i) => `
              <label style="cursor:pointer; padding:1rem; border:2px solid #e2e8f0; border-radius:8px; transition:all 0.2s; display:flex; align-items:center; gap:0.75rem;"
                     onmouseover="this.style.borderColor='#0067A5'; this.style.background='#f7fafc'" 
                     onmouseout="if(!this.querySelector('input').checked) { this.style.borderColor='#e2e8f0'; this.style.background='white' }">
                <input type="radio" name="question_${
                  question.question_id
                }" value="${option}" ${question.required ? "required" : ""}
                       onchange="saveAnswer('${
                         question.question_id
                       }', this.value); this.parentElement.parentElement.querySelectorAll('label').forEach(l => { l.style.borderColor='#e2e8f0'; l.style.background='white' }); this.parentElement.style.borderColor='#0067A5'; this.parentElement.style.background='#ebf8ff'">
                <span style="color:#2d3748; font-weight:500;">${option}</span>
              </label>
            `
              )
              .join("")}
          </div>
        `;
      break;

    case "text":
      html += `
          <textarea name="question_${question.question_id}" rows="5" ${
        question.required ? "required" : ""
      }
                    onchange="saveAnswer('${question.question_id}', this.value)"
                    placeholder="Type your answer here..."
                    style="width:100%; padding:1rem; border:2px solid #e2e8f0; border-radius:8px; font-size:0.95rem; font-family:inherit; resize:vertical;"
                    onfocus="this.style.borderColor='#0067A5'" onblur="this.style.borderColor='#e2e8f0'"></textarea>
        `;
      break;

    case "yes_no":
      html += `
          <div style="display:flex; gap:1rem;">
            ${["Yes", "No"]
              .map(
                (option) => `
              <label style="cursor:pointer; padding:1rem 2rem; border:2px solid #e2e8f0; border-radius:8px; transition:all 0.2s; flex:1; text-align:center; font-weight:500;"
                     onmouseover="this.style.borderColor='#0067A5'; this.style.background='#f7fafc'" 
                     onmouseout="if(!this.querySelector('input').checked) { this.style.borderColor='#e2e8f0'; this.style.background='white' }">
                <input type="radio" name="question_${
                  question.question_id
                }" value="${option}" ${question.required ? "required" : ""}
                       onchange="saveAnswer('${
                         question.question_id
                       }', this.value); this.parentElement.parentElement.querySelectorAll('label').forEach(l => { l.style.borderColor='#e2e8f0'; l.style.background='white' }); this.parentElement.style.borderColor='#0067A5'; this.parentElement.style.background='#ebf8ff'"
                       style="display:none;">
                ${option}
              </label>
            `
              )
              .join("")}
          </div>
        `;
      break;
  }

  html += "</div></div>";
  return html;
}

// Save answer
function saveAnswer(questionId, answer) {
  surveyAnswers[questionId] = answer;
}

// Restore previous answer
function restoreAnswer(question, answer) {
  const input = document.querySelector(
    `[name="question_${question.question_id}"]`
  );
  if (input) {
    if (input.type === "radio") {
      const radio = document.querySelector(
        `[name="question_${question.question_id}"][value="${answer}"]`
      );
      if (radio) {
        radio.checked = true;
        radio.dispatchEvent(new Event("change"));
      }
    } else if (input.tagName === "TEXTAREA") {
      input.value = answer;
    }
  }
}

// Next question
document.getElementById("next-question-btn").addEventListener("click", () => {
  const currentQuestion = surveyQuestions[currentQuestionIndex];
  if (currentQuestion.required && !surveyAnswers[currentQuestion.question_id]) {
    alert("Please answer this question before proceeding.");
    return;
  }
  showQuestion(currentQuestionIndex + 1);
});

// Previous question
document.getElementById("prev-question-btn").addEventListener("click", () => {
  showQuestion(currentQuestionIndex - 1);
});

// Submit survey
document.getElementById("survey-form").addEventListener("submit", async (e) => {
  e.preventDefault();

  // Check if all required questions are answered
  const requiredQuestions = surveyQuestions.filter((q) => q.required);
  const missingAnswers = requiredQuestions.filter(
    (q) => !surveyAnswers[q.question_id]
  );

  if (missingAnswers.length > 0) {
    alert("Please answer all required questions.");
    return;
  }

  try {
    // Convert answers object to array format
    const answersArray = Object.keys(surveyAnswers).map((questionId) => ({
      question_id: questionId,
      answer: surveyAnswers[questionId],
    }));

    console.log("Submitting survey:", currentSurveyId, answersArray); // Debug log

    const response = await fetch(`/api/surveys/${currentSurveyId}/submit`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ answers: answersArray }),
    });

    const result = await response.json();

    if (!response.ok) {
      console.error("Server error:", result);
      throw new Error(result.detail || "Failed to submit survey");
    }

    // Show success message
    alert("Survey submitted successfully! Thank you for your feedback.");

    closeTakeSurveyModal();
    loadFeedbackCount(); // Update the feedback count
    openSurveysModal(); // Reload surveys list
  } catch (error) {
    console.error("Error submitting survey:", error);
    alert(error.message || "Error submitting survey. Please try again.");
  }
});
