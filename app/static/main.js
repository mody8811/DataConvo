// Get elements
const signupBtn = document.getElementById('signup-btn');
const modal = document.getElementById('signup-modal');
const closeModal = document.getElementById('close-modal');
const signupForm = document.getElementById('signup-form');

// Show the modal when "Sign Up" is clicked
signupBtn.addEventListener('click', () => {
    modal.style.display = 'flex';
    setTimeout(() => {
        modal.style.opacity = '1';
        modal.querySelector('.modal-content').style.transform = 'translateY(0)';
    }, 10);
});

// Hide the modal when the close button is clicked
closeModal.addEventListener('click', () => {
    modal.style.opacity = '0';
    modal.querySelector('.modal-content').style.transform = 'translateY(-20px)';
    setTimeout(() => {
        modal.style.display = 'none';
    }, 300);
});

// Hide the modal when clicking outside of it
window.addEventListener('click', (e) => {
    if (e.target === modal) {
        modal.style.opacity = '0';
        modal.querySelector('.modal-content').style.transform = 'translateY(-20px)';
        setTimeout(() => {
            modal.style.display = 'none';
        }, 300);
    }
});

// Handle form submission
signupForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const formData = new FormData(signupForm);
    const response = await fetch('/signup', {
        method: 'POST',
        body: formData
    });
    const result = await response.json();
    if (result.success) {
        alert('Signup successful!');
        window.location.href = '/dashboard'; // Redirect to dashboard or another page
    } else {
        alert('Signup failed: ' + result.message);
    }
});