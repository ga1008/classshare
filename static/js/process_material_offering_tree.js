const UNKNOWN_CLASS = '未命名班级';
const UNKNOWN_COURSE = '未命名课程';
const UNKNOWN_SEMESTER = '未分配学期';

export function getProcessMaterialClassDisplayName(offering) {
    return offering?.display_class_name
        || offering?.academic_class_name
        || offering?.class_name
        || UNKNOWN_CLASS;
}

export function formatProcessMaterialOfferingOptionLabel(offering, { includeSemester = false } = {}) {
    const parts = [
        offering?.course_name || UNKNOWN_COURSE,
        getProcessMaterialClassDisplayName(offering),
    ];
    if (includeSemester && offering?.semester_label) parts.push(offering.semester_label);
    return parts.join(' · ');
}

export function getProcessMaterialSemesterSortValue(label, startDate) {
    if (startDate) {
        const timestamp = Date.parse(startDate);
        if (!Number.isNaN(timestamp)) return timestamp;
    }
    const text = String(label || '');
    const yearMatch = text.match(/(20\d{2})\D+(20\d{2})/);
    const endYear = yearMatch ? Number(yearMatch[2]) : 0;
    const singleYear = !yearMatch && text.match(/20\d{2}/) ? Number(text.match(/20\d{2}/)[0]) : 0;
    const year = endYear || singleYear;
    let term = 0;
    if (/第二|下|春|(?:^|[^\d])2(?:[^\d]|$)/.test(text)) term = 2;
    else if (/第一|上|秋|(?:^|[^\d])1(?:[^\d]|$)/.test(text)) term = 1;
    return year ? year * 10 + term : 0;
}

export function buildProcessMaterialOfferingTree(offerings) {
    const bySemester = new Map();
    for (const offering of Array.isArray(offerings) ? offerings : []) {
        const semesterKey = offering?.semester_label || UNKNOWN_SEMESTER;
        if (!bySemester.has(semesterKey)) {
            bySemester.set(semesterKey, {
                label: semesterKey,
                sortKey: getProcessMaterialSemesterSortValue(semesterKey, offering?.semester_start_date),
                courses: new Map(),
            });
        }
        const semester = bySemester.get(semesterKey);
        const nextSort = getProcessMaterialSemesterSortValue(semesterKey, offering?.semester_start_date);
        if (nextSort > semester.sortKey) semester.sortKey = nextSort;
        const courseKey = offering?.course_name || UNKNOWN_COURSE;
        if (!semester.courses.has(courseKey)) semester.courses.set(courseKey, []);
        semester.courses.get(courseKey).push(offering);
    }
    const semesters = [...bySemester.values()].sort(
        (a, b) => (b.sortKey - a.sortKey) || b.label.localeCompare(a.label, 'zh')
    );
    return semesters.map((semester) => ({
        label: semester.label,
        badge: `${semester.courses.size} 门课程`,
        children: [...semester.courses.entries()]
            .sort((a, b) => a[0].localeCompare(b[0], 'zh'))
            .map(([courseName, courseOfferings]) => ({
                label: courseName,
                badge: `${courseOfferings.length} 个班级`,
                children: courseOfferings
                    .slice()
                    .sort((a, b) => getProcessMaterialClassDisplayName(a).localeCompare(
                        getProcessMaterialClassDisplayName(b),
                        'zh'
                    ))
                    .map((offering) => ({
                        label: getProcessMaterialClassDisplayName(offering),
                        leaf: true,
                        data: offering,
                    })),
            })),
    }));
}
