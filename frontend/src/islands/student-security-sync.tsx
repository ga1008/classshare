import { useEffect } from 'react';

import { mountReactIslandsWhenReady } from '@/lib/mount-react-island';
import {
  passwordChangeErrorMessage,
  passwordChangeSuccessMessage,
  STUDENT_PASSWORD_CHANGE_FORM_ID,
  STUDENT_PASSWORD_CHANGE_URL,
  STUDENT_SECURITY_ERROR_FALLBACK,
  STUDENT_SECURITY_MODAL_ID,
  STUDENT_SECURITY_PENDING_TEXT,
  STUDENT_SECURITY_TRIGGER_SELECTOR,
} from '@/lib/student-security';

function showToast(message: string, type: 'success' | 'error') {
  if (typeof window.UI?.showToast === 'function') {
    window.UI.showToast(message, type);
    return;
  }
  if (typeof window.showMessage === 'function') {
    window.showMessage(message, type);
  }
}

function openStudentSecurityModal() {
  if (typeof window.UI?.openModal === 'function') {
    window.UI.openModal(STUDENT_SECURITY_MODAL_ID);
    return;
  }

  const modalOverlay = document.getElementById(STUDENT_SECURITY_MODAL_ID);
  if (!modalOverlay) {
    return;
  }
  modalOverlay.style.display = 'flex';
  window.requestAnimationFrame(() => {
    modalOverlay.classList.add('show');
  });
  document.body.style.overflow = 'hidden';
}

function closeStudentSecurityModal() {
  if (typeof window.UI?.closeModal === 'function') {
    window.UI.closeModal(STUDENT_SECURITY_MODAL_ID);
    return;
  }

  const modalOverlay = document.getElementById(STUDENT_SECURITY_MODAL_ID);
  if (!modalOverlay) {
    return;
  }
  modalOverlay.classList.remove('show');
  window.setTimeout(() => {
    modalOverlay.style.display = 'none';
    document.body.style.overflow = '';
  }, 300);
}

function setSubmitting(button: HTMLButtonElement | null, submitting: boolean) {
  if (!button) {
    return;
  }

  if (submitting) {
    button.dataset.originalText = button.innerHTML;
    button.disabled = true;
    button.textContent = STUDENT_SECURITY_PENDING_TEXT;
    return;
  }

  button.disabled = false;
  if (button.dataset.originalText) {
    button.innerHTML = button.dataset.originalText;
  }
}

async function parseResponsePayload(response: Response): Promise<unknown> {
  const contentType = response.headers.get('content-type') || '';
  if (contentType.includes('application/json')) {
    return response.json();
  }
  const text = await response.text();
  return text || null;
}

async function submitPasswordChangeForm(form: HTMLFormElement) {
  const submitButton = form.querySelector<HTMLButtonElement>('button[type="submit"]');
  setSubmitting(submitButton, true);
  try {
    const response = await fetch(STUDENT_PASSWORD_CHANGE_URL, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
      body: new FormData(form),
    });
    const payload = await parseResponsePayload(response);
    if (!response.ok) {
      throw new Error(passwordChangeErrorMessage(payload));
    }

    showToast(passwordChangeSuccessMessage(payload), 'success');
    form.reset();
    closeStudentSecurityModal();
  } catch (error) {
    showToast(error instanceof Error ? error.message : STUDENT_SECURITY_ERROR_FALLBACK, 'error');
  } finally {
    setSubmitting(submitButton, false);
  }
}

function useStudentSecuritySync() {
  useEffect(() => {
    const modal = document.getElementById(STUDENT_SECURITY_MODAL_ID);
    const form = document.getElementById(STUDENT_PASSWORD_CHANGE_FORM_ID) as HTMLFormElement | null;
    if (!modal && !form) {
      return undefined;
    }

    if (modal) {
      modal.dataset.studentSecurityManaged = 'react';
    }
    if (form) {
      form.dataset.studentSecurityManaged = 'react';
    }
    document.querySelectorAll<HTMLElement>(STUDENT_SECURITY_TRIGGER_SELECTOR).forEach((trigger) => {
      trigger.dataset.studentSecurityManaged = 'react';
    });

    const handleClick = (event: MouseEvent) => {
      const target = event.target instanceof Element ? event.target : null;
      const trigger = target?.closest<HTMLElement>(STUDENT_SECURITY_TRIGGER_SELECTOR);
      if (!trigger) {
        return;
      }
      event.preventDefault();
      openStudentSecurityModal();
    };

    const handleSubmit = (event: SubmitEvent) => {
      if (event.target !== form || !form) {
        return;
      }
      event.preventDefault();
      void submitPasswordChangeForm(form);
    };

    document.addEventListener('click', handleClick);
    document.addEventListener('submit', handleSubmit);

    return () => {
      modal?.removeAttribute('data-student-security-managed');
      form?.removeAttribute('data-student-security-managed');
      document.querySelectorAll<HTMLElement>(STUDENT_SECURITY_TRIGGER_SELECTOR).forEach((trigger) => {
        trigger.removeAttribute('data-student-security-managed');
      });
      document.removeEventListener('click', handleClick);
      document.removeEventListener('submit', handleSubmit);
    };
  }, []);
}

function StudentSecuritySyncIsland() {
  useStudentSecuritySync();
  return null;
}

mountReactIslandsWhenReady({
  islandName: 'student-security-sync',
  defaultMountIdPrefix: 'student-security-sync',
  getProps: () => ({}),
  render: () => <StudentSecuritySyncIsland />,
});
