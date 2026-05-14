STUDENT_STATUS_ACTIVE = "active"
STUDENT_STATUS_SUSPENDED = "suspended"

STUDENT_STATUS_LABELS = {
    STUDENT_STATUS_ACTIVE: "在读",
    STUDENT_STATUS_SUSPENDED: "休学",
}


def normalize_student_enrollment_status(value) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"suspended", "休学"}:
        return STUDENT_STATUS_SUSPENDED
    return STUDENT_STATUS_ACTIVE


def student_enrollment_status_label(value) -> str:
    return STUDENT_STATUS_LABELS.get(
        normalize_student_enrollment_status(value),
        STUDENT_STATUS_LABELS[STUDENT_STATUS_ACTIVE],
    )


def is_active_student_status(value) -> bool:
    return normalize_student_enrollment_status(value) == STUDENT_STATUS_ACTIVE
