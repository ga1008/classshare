import { apiFetch } from '/static/js/api.js';
import { showMessage } from '/static/js/ui.js';

const elements = {
    form: document.getElementById('classCreateForm'),
    classNameInput: document.getElementById('classNameInput'),
    createButtons: [
        document.getElementById('focusClassCreateBtn'),
        document.getElementById('heroClassCreateBtn'),
    ].filter(Boolean),
    classList: document.getElementById('classList'),
};

function focusCreateForm() {
    if (!elements.classNameInput) {
        return;
    }
    elements.classNameInput.scrollIntoView({ behavior: 'smooth', block: 'center' });
    window.setTimeout(() => {
        elements.classNameInput.focus();
    }, 180);
}

async function handleDelete(button) {
    const classId = Number(button.dataset.classId || 0);
    const className = String(button.dataset.className || '').trim() || '当前班级';
    if (!classId) {
        return;
    }

    const confirmed = window.confirm(
        `确定删除班级“${className}”吗？\n这会同时删除该班级下的学生和与课堂的关联记录。`
    );
    if (!confirmed) {
        return;
    }

    try {
        const result = await apiFetch(`/api/manage/classes/${classId}`, {
            method: 'DELETE',
            silent: true,
        });
        showMessage(result.message || '班级已删除', 'success');
        window.location.reload();
    } catch (error) {
        showMessage(error.message || '删除班级失败', 'error');
    }
}

function bindEvents() {
    elements.form?.addEventListener('submit', (event) => {
        window.handleFormSubmit(event);
    });

    elements.createButtons.forEach((button) => {
        button.addEventListener('click', focusCreateForm);
    });

    elements.classList?.addEventListener('click', (event) => {
        const deleteButton = event.target.closest('[data-action="delete-class"]');
        if (deleteButton) {
            handleDelete(deleteButton);
        }
    });
}

bindEvents();
