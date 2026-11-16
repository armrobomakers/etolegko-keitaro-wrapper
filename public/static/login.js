const form = document.querySelector("#login-form");
const errorBox = document.querySelector("#login-error");

form?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = form.querySelector("button[type=submit]");
  button.disabled = true;
  errorBox.classList.add("hidden");
  try {
    const formData = new FormData(form);
    const response = await fetch("/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: formData.get("username"), password: formData.get("password") }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "Не удалось войти");
    window.location.replace(data.redirect || "/");
  } catch (error) {
    errorBox.textContent = error.message || "Не удалось связаться с приложением";
    errorBox.classList.remove("hidden");
    button.disabled = false;
  }
});
