import { apiFetch } from '/static/js/api.js';
import { showMessage } from '/static/js/ui.js';

const config = window.TEXTBOOK_MANAGE_DATA || {};

const state = {
    textbooks: Array.isArray(config.textbooks) ? config.textbooks.map(normalizeTextbook) : [],
    filters: {
        search: '',
        publisher: '',
        tag: '',
        attachment: '',
    },
    modalAuthors: [],
    modalTags: [],
    editingTextbookId: null,
    removeAttachment: false,
    aiFormatting: false,
    rawIntroduction: '',
    rawCatalog: '',
};

const elements = {
    cardGrid: document.getElementById('textbookCardGrid'),
    emptyState: document.getElementById('textbookEmptyState'),
    searchInput: document.getElementById('textbookSearchInput'),
    clearFiltersBtn: document.getElementById('textbookClearFiltersBtn'),
    publisherFilter: document.getElementById('textbookPublisherFilter'),
    tagFilter: document.getElementById('textbookTagFilter'),
    attachmentFilter: document.getElementById('textbookAttachmentFilter'),
    openCreateBtns: [
        document.getElementById('openTextbookCreateBtn'),
        document.getElementById('heroTextbookCreateBtn'),
    ].filter(Boolean),
    modalBackdrop: document.getElementById('textbookModalBackdrop'),
    modalTitle: document.getElementById('textbookModalTitle'),
    modalCloseBtn: document.getElementById('textbookModalCloseBtn'),
    modalCancelBtn: document.getElementById('textbookModalCancelBtn'),
    form: document.getElementById('textbookForm'),
    submitBtn: document.getElementById('textbookSubmitBtn'),
    idInput: document.getElementById('textbookIdInput'),
    titleInput: document.getElementById('textbookTitleInput'),
    publisherInput: document.getElementById('textbookPublisherInput'),
    publicationDateInput: document.getElementById('textbookPublicationDateInput'),
    introductionInput: document.getElementById('textbookIntroductionInput'),
    catalogInput: document.getElementById('textbookCatalogInput'),
    attachmentInput: document.getElementById('textbookAttachmentInput'),
    authorsJsonInput: document.getElementById('textbookAuthorsJsonInput'),
    tagsJsonInput: document.getElementById('textbookTagsJsonInput'),
    removeAttachmentInput: document.getElementById('textbookRemoveAttachmentInput'),
    authorChipList: document.getElementById('textbookAuthorChipList'),
    authorInput: document.getElementById('textbookAuthorInput'),
    authorAddBtn: document.getElementById('textbookAuthorAddBtn'),
    tagChipList: document.getElementById('textbookTagChipList'),
    tagInput: document.getElementById('textbookTagInput'),
    tagAddBtn: document.getElementById('textbookTagAddBtn'),
    existingAttachmentRow: document.getElementById('textbookExistingAttachmentRow'),
    existingAttachmentName: document.getElementById('textbookExistingAttachmentName'),
    existingAttachmentHint: document.getElementById('textbookExistingAttachmentHint'),
    attachmentDownloadLink: document.getElementById('textbookAttachmentDownloadLink'),
    removeAttachmentBtn: document.getElementById('textbookRemoveAttachmentBtn'),
    openIntroCatalogBtn: document.getElementById('textbookOpenIntroCatalogBtn'),
    introCatalogStatus: document.getElementById('textbookIntroCatalogStatus'),
    formattedResult: document.getElementById('textbookFormattedResult'),
    formattedIntroSection: document.getElementById('textbookFormattedIntroSection'),
    formattedCatalogSection: document.getElementById('textbookFormattedCatalogSection'),
    formattedIntro: document.getElementById('textbookFormattedIntro'),
    formattedCatalog: document.getElementById('textbookFormattedCatalog'),
    reformatBtn: document.getElementById('textbookReformatBtn'),
    introCatalogBackdrop: document.getElementById('textbookIntroCatalogBackdrop'),
    introCatalogCloseBtn: document.getElementById('textbookIntroCatalogCloseBtn'),
    introCatalogCancelBtn: document.getElementById('textbookIntroCatalogCancelBtn'),
    introCatalogConfirmBtn: document.getElementById('textbookIntroCatalogConfirmBtn'),
    rawIntroInput: document.getElementById('textbookRawIntroInput'),
    rawCatalogInput: document.getElementById('textbookRawCatalogInput'),
    customRequirementsInput: document.getElementById('textbookCustomRequirementsInput'),
};

function normalizeTextbook(item) {
    return {
        ...item,
        id: Number(item.id),
        authors: Array.isArray(item.authors) ? item.authors : [],
        tags: Array.isArray(item.tags) ? item.tags : [],
        publication_year: item.publication_year ? Number(item.publication_year) : null,
        has_attachment: Boolean(item.has_attachment),
        attachment_size: Number(item.attachment_size || 0),
        search_blob: String(item.search_blob || '').toLowerCase(),
        updated_at: String(item.updated_at || ''),
    };
}

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function formatFileSize(bytes) {
    if (!bytes) return '未知大小';
    const units = ['B', 'KB', 'MB', 'GB'];
    let size = Number(bytes);
    let unitIndex = 0;
    while (size >= 1024 && unitIndex < units.length - 1) {
        size /= 1024;
        unitIndex += 1;
    }
    return `${size.toFixed(size >= 10 || unitIndex === 0 ? 0 : 1)} ${units[unitIndex]}`;
}

function getUniqueValues(mapper) {
    return Array.from(new Set(state.textbooks.map(mapper).filter(Boolean))).sort((a, b) => String(a).localeCompare(String(b), 'zh-CN'));
}

function renderFilterOptions() {
    if (elements.publisherFilter) {
        const publishers = getUniqueValues((item) => item.publisher);
        elements.publisherFilter.innerHTML = '<option value="">全部出版社</option>' + publishers.map((publisher) => `
            <option value="${escapeHtml(publisher)}">${escapeHtml(publisher)}</option>
        `).join('');
        elements.publisherFilter.value = state.filters.publisher;
    }

    if (elements.tagFilter) {
        const tags = Array.from(new Set(state.textbooks.flatMap((item) => item.tags))).sort((a, b) => a.localeCompare(b, 'zh-CN'));
        elements.tagFilter.innerHTML = '<option value="">全部标签</option>' + tags.map((tag) => `
            <option value="${escapeHtml(tag)}">${escapeHtml(tag)}</option>
        `).join('');
        elements.tagFilter.value = state.filters.tag;
    }
}

function getFilteredTextbooks() {
    return state.textbooks.filter((item) => {
        if (state.filters.search && !item.search_blob.includes(state.filters.search.toLowerCase())) {
            return false;
        }
        if (state.filters.publisher && item.publisher !== state.filters.publisher) {
            return false;
        }
        if (state.filters.tag && !item.tags.includes(state.filters.tag)) {
            return false;
        }
        if (state.filters.attachment === 'has' && !item.has_attachment) {
            return false;
        }
        if (state.filters.attachment === 'none' && item.has_attachment) {
            return false;
        }
        return true;
    });
}

function renderCards() {
    if (!elements.cardGrid) return;
    const items = getFilteredTextbooks();
    elements.cardGrid.innerHTML = items.map((item) => `
        <article class="academic-resource-card" id="textbook-card-${item.id}">
            <div class="academic-card-topline">
                <div class="academic-card-main">
                    <span class="academic-card-kicker">教材</span>
                    <h3>${escapeHtml(item.title || '未命名教材')}</h3>
                    <p>${escapeHtml(item.author_display || item.authors.join('、') || '未填写作者')}</p>
                </div>
                <div class="academic-badge-row">
                    ${item.publication_year ? `<span class="academic-badge">${item.publication_year} 年</span>` : ''}
                    ${item.has_attachment ? '<span class="academic-badge is-success">带附件</span>' : '<span class="academic-badge is-muted">无附件</span>'}
                </div>
            </div>
            <div class="academic-meta-list">
                <div class="academic-meta-row">
                    <span class="academic-meta-label">出版社</span>
                    <span class="academic-meta-value">${escapeHtml(item.publisher || '未填写')}</span>
                </div>
                <div class="academic-meta-row">
                    <span class="academic-meta-label">更新时间</span>
                    <span class="academic-meta-value">${escapeHtml(item.updated_at || '暂无')}</span>
                </div>
            </div>
            <p class="textbook-description">${escapeHtml(item.introduction_preview || item.introduction || '暂无教材简介。')}</p>
            <div class="textbook-catalog-preview">${escapeHtml(item.catalog_preview || item.catalog_text || '暂无目录信息。')}</div>
            <div class="academic-tag-row">
                ${(item.tags || []).map((tag) => `<span class="academic-tag">${escapeHtml(tag)}</span>`).join('') || '<span class="academic-badge is-muted">未设置标签</span>'}
            </div>
            <div class="academic-card-actions">
                <div class="academic-action-group">
                    <button type="button" class="btn btn-outline btn-sm" data-action="edit" data-textbook-id="${item.id}">编辑</button>
                    ${item.has_attachment ? `<a class="btn btn-ghost btn-sm" href="/api/manage/textbooks/${item.id}/attachment" target="_blank" rel="noopener">下载附件</a>` : ''}
                </div>
                <button type="button" class="btn btn-danger btn-sm" data-action="delete" data-textbook-id="${item.id}">删除</button>
            </div>
        </article>
    `).join('');

    if (elements.emptyState) {
        elements.emptyState.hidden = items.length > 0;
    }
}

function renderChipList(container, values, type) {
    if (!container) return;
    if (!values.length) {
        container.innerHTML = `<span class="academic-badge is-muted">暂无${type === 'author' ? '作者' : '标签'}</span>`;
        return;
    }
    container.innerHTML = values.map((value, index) => `
        <span class="academic-chip">
            ${escapeHtml(value)}
            <button type="button" data-chip-type="${type}" data-chip-index="${index}" aria-label="删除">&times;</button>
        </span>
    `).join('');
}

function syncChipPayloads() {
    elements.authorsJsonInput.value = JSON.stringify(state.modalAuthors);
    elements.tagsJsonInput.value = JSON.stringify(state.modalTags);
    renderChipList(elements.authorChipList, state.modalAuthors, 'author');
    renderChipList(elements.tagChipList, state.modalTags, 'tag');
}

function openModal(mode, textbook = null) {
    elements.modalTitle.textContent = mode === 'edit' ? '编辑教材' : '新增教材';
    elements.submitBtn.textContent = mode === 'edit' ? '保存修改' : '保存教材';
    state.editingTextbookId = textbook?.id ?? null;
    state.modalAuthors = textbook?.authors ? [...textbook.authors] : [];
    state.modalTags = textbook?.tags ? [...textbook.tags] : [];
    state.removeAttachment = false;
    state.aiFormatting = false;
    state.rawIntroduction = '';
    state.rawCatalog = '';

    elements.idInput.value = textbook?.id ? String(textbook.id) : '';
    elements.titleInput.value = textbook?.title || '';
    elements.publisherInput.value = textbook?.publisher || '';
    elements.publicationDateInput.value = textbook?.publication_date || '';
    elements.introductionInput.value = textbook?.introduction || '';
    elements.catalogInput.value = textbook?.catalog_text || '';
    elements.attachmentInput.value = '';
    elements.removeAttachmentInput.value = 'false';
    elements.authorInput.value = '';
    elements.tagInput.value = '';

    // Reset intro/catalog UI
    if (elements.openIntroCatalogBtn) {
        elements.openIntroCatalogBtn.disabled = false;
        elements.openIntroCatalogBtn.textContent = '简介与目录';
    }
    if (elements.introCatalogStatus) {
        elements.introCatalogStatus.hidden = true;
    }
    if (elements.formattedResult) {
        elements.formattedResult.hidden = true;
    }

    // If editing and has existing intro/catalog, show formatted result
    const existingIntro = textbook?.introduction || '';
    const existingCatalog = textbook?.catalog_text || '';
    if (existingIntro || existingCatalog) {
        state.rawIntroduction = existingIntro;
        state.rawCatalog = existingCatalog;
        showFormattedResult(existingIntro, existingCatalog);
    }

    renderExistingAttachment(textbook);
    syncChipPayloads();
    elements.modalBackdrop.classList.add('is-open');
}

function closeModal() {
    elements.modalBackdrop.classList.remove('is-open');
}

function renderExistingAttachment(textbook) {
    if (!elements.existingAttachmentRow) return;
    const hasExisting = textbook && textbook.has_attachment && !state.removeAttachment;
    elements.existingAttachmentRow.hidden = !hasExisting;
    if (!hasExisting) return;

    elements.existingAttachmentName.textContent = textbook.attachment_name || '当前附件';
    elements.existingAttachmentHint.textContent = `大小：${formatFileSize(textbook.attachment_size)} · 若上传新文件会自动替换。`;
    elements.attachmentDownloadLink.href = `/api/manage/textbooks/${textbook.id}/attachment`;
}

function addChip(type) {
    const input = type === 'author' ? elements.authorInput : elements.tagInput;
    const target = type === 'author' ? state.modalAuthors : state.modalTags;
    const limit = type === 'author' ? 12 : 20;
    const maxLength = type === 'author' ? 30 : 12;
    const rawValue = String(input.value || '').trim();
    if (!rawValue) return;
    if (rawValue.length > maxLength) {
        showMessage(`${type === 'author' ? '作者' : '标签'}长度不能超过 ${maxLength} 个字符`, 'warning');
        return;
    }
    if (target.includes(rawValue)) {
        showMessage(`该${type === 'author' ? '作者' : '标签'}已存在`, 'warning');
        return;
    }
    if (target.length >= limit) {
        showMessage(`${type === 'author' ? '作者' : '标签'}数量不能超过 ${limit} 项`, 'warning');
        return;
    }
    target.push(rawValue);
    input.value = '';
    syncChipPayloads();
}

async function handleDelete(textbookId) {
    const textbook = state.textbooks.find((item) => item.id === Number(textbookId));
    if (!textbook) return;
    const confirmed = window.confirm(`确定删除教材“${textbook.title}”吗？\n如果它已经绑定到课堂，需要先调整课堂绑定。`);
    if (!confirmed) return;

    const result = await apiFetch(`/api/manage/textbooks/${textbook.id}`, { method: 'DELETE' });
    showMessage(result.message || '教材已删除', 'success');
    window.location.reload();
}

async function handleSubmit(event) {
    event.preventDefault();
    if (!elements.form || !elements.submitBtn) return;

    if (state.aiFormatting) {
        showMessage('AI 正在整理简介与目录，请稍候', 'warning');
        return;
    }

    if (!String(elements.titleInput.value || '').trim()) {
        showMessage('教材名称不能为空', 'warning');
        return;
    }

    syncChipPayloads();
    elements.removeAttachmentInput.value = state.removeAttachment ? 'true' : 'false';

    const formData = new FormData(elements.form);
    const originalText = elements.submitBtn.textContent;
    elements.submitBtn.disabled = true;
    elements.submitBtn.textContent = '正在保存...';

    try {
        const result = await apiFetch(elements.form.action, {
            method: 'POST',
            body: formData,
        });
        showMessage(result.message || '教材已保存', 'success');
        window.location.reload();
    } catch (error) {
        showMessage(error.message || '教材保存失败', 'error');
    } finally {
        elements.submitBtn.disabled = false;
        elements.submitBtn.textContent = originalText;
    }
}

function showFormattedResult(introduction, catalogText) {
    if (!elements.formattedResult) return;
    elements.formattedResult.hidden = false;

    if (elements.formattedIntroSection) {
        elements.formattedIntroSection.hidden = !introduction;
    }
    if (elements.formattedCatalogSection) {
        elements.formattedCatalogSection.hidden = !catalogText;
    }
    if (elements.formattedIntro) {
        elements.formattedIntro.textContent = introduction || '';
    }
    if (elements.formattedCatalog) {
        elements.formattedCatalog.textContent = catalogText || '';
    }
}

function openIntroCatalogPopup() {
    // Pre-populate with raw content (for re-editing)
    if (elements.rawIntroInput) {
        elements.rawIntroInput.value = state.rawIntroduction;
    }
    if (elements.rawCatalogInput) {
        elements.rawCatalogInput.value = state.rawCatalog;
    }
    if (elements.customRequirementsInput) {
        elements.customRequirementsInput.value = '';
    }
    if (elements.introCatalogBackdrop) {
        elements.introCatalogBackdrop.classList.add('is-open');
    }
}

function closeIntroCatalogPopup() {
    if (elements.introCatalogBackdrop) {
        elements.introCatalogBackdrop.classList.remove('is-open');
    }
}

async function handleAiFormatIntroCatalog() {
    const rawIntro = String(elements.rawIntroInput?.value || '').trim();
    const rawCatalog = String(elements.rawCatalogInput?.value || '').trim();
    const customReqs = String(elements.customRequirementsInput?.value || '').trim();

    if (!rawIntro && !rawCatalog) {
        showMessage('请至少填写教材简介或教材目录', 'warning');
        return;
    }

    // Save raw content for re-editing
    state.rawIntroduction = rawIntro;
    state.rawCatalog = rawCatalog;

    // Close popup, show loading state
    closeIntroCatalogPopup();

    if (elements.openIntroCatalogBtn) {
        elements.openIntroCatalogBtn.disabled = true;
        elements.openIntroCatalogBtn.textContent = '简介与目录';
    }
    if (elements.introCatalogStatus) {
        elements.introCatalogStatus.hidden = false;
    }
    state.aiFormatting = true;

    try {
        const formData = new FormData();
        formData.append('title', elements.titleInput?.value || '');
        formData.append('publisher', elements.publisherInput?.value || '');
        formData.append('authors_json', elements.authorsJsonInput?.value || '[]');
        formData.append('publication_date', elements.publicationDateInput?.value || '');
        formData.append('tags_json', elements.tagsJsonInput?.value || '[]');
        formData.append('raw_introduction', rawIntro);
        formData.append('raw_catalog', rawCatalog);
        formData.append('custom_requirements', customReqs);

        // Include attachment file if selected in the main form
        const attachmentFiles = elements.attachmentInput?.files;
        if (attachmentFiles && attachmentFiles.length > 0) {
            formData.append('attachment', attachmentFiles[0]);
        }

        const result = await apiFetch('/api/manage/textbooks/ai-format-intro-catalog', {
            method: 'POST',
            body: formData,
        });

        const intro = String(result.introduction || '').trim();
        const catalog = String(result.catalog_text || '').trim();

        // Update hidden inputs
        if (elements.introductionInput) {
            elements.introductionInput.value = intro;
        }
        if (elements.catalogInput) {
            elements.catalogInput.value = catalog;
        }

        showFormattedResult(intro, catalog);
        showMessage('简介与目录已整理完成', 'success');
    } catch (error) {
        showMessage(error.message || 'AI 整理失败，请重试', 'error');
    } finally {
        state.aiFormatting = false;
        if (elements.openIntroCatalogBtn) {
            elements.openIntroCatalogBtn.disabled = false;
            elements.openIntroCatalogBtn.textContent = '简介与目录';
        }
        if (elements.introCatalogStatus) {
            elements.introCatalogStatus.hidden = true;
        }
    }
}

function initEvents() {
    renderFilterOptions();
    renderCards();

    elements.openCreateBtns.forEach((button) => {
        button.addEventListener('click', () => openModal('create'));
    });

    elements.modalCloseBtn?.addEventListener('click', closeModal);
    elements.modalCancelBtn?.addEventListener('click', closeModal);
    elements.modalBackdrop?.addEventListener('click', (event) => {
        if (event.target === elements.modalBackdrop) closeModal();
    });

    elements.searchInput?.addEventListener('input', (event) => {
        state.filters.search = String(event.target.value || '').trim();
        renderCards();
    });
    elements.publisherFilter?.addEventListener('change', (event) => {
        state.filters.publisher = String(event.target.value || '');
        renderCards();
    });
    elements.tagFilter?.addEventListener('change', (event) => {
        state.filters.tag = String(event.target.value || '');
        renderCards();
    });
    elements.attachmentFilter?.addEventListener('change', (event) => {
        state.filters.attachment = String(event.target.value || '');
        renderCards();
    });
    elements.clearFiltersBtn?.addEventListener('click', () => {
        state.filters = { search: '', publisher: '', tag: '', attachment: '' };
        if (elements.searchInput) elements.searchInput.value = '';
        if (elements.publisherFilter) elements.publisherFilter.value = '';
        if (elements.tagFilter) elements.tagFilter.value = '';
        if (elements.attachmentFilter) elements.attachmentFilter.value = '';
        renderCards();
    });

    elements.cardGrid?.addEventListener('click', async (event) => {
        const target = event.target.closest('[data-action]');
        if (!target) return;
        const textbookId = Number(target.dataset.textbookId || 0);
        const textbook = state.textbooks.find((item) => item.id === textbookId);
        if (!textbook) return;

        if (target.dataset.action === 'edit') {
            openModal('edit', textbook);
            return;
        }
        if (target.dataset.action === 'delete') {
            await handleDelete(textbookId);
        }
    });

    elements.authorAddBtn?.addEventListener('click', () => addChip('author'));
    elements.tagAddBtn?.addEventListener('click', () => addChip('tag'));
    elements.authorInput?.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') {
            event.preventDefault();
            addChip('author');
        }
    });
    elements.tagInput?.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') {
            event.preventDefault();
            addChip('tag');
        }
    });

    document.addEventListener('click', (event) => {
        const button = event.target.closest('[data-chip-type]');
        if (!button) return;
        const chipType = button.dataset.chipType;
        const chipIndex = Number(button.dataset.chipIndex || -1);
        if (chipIndex < 0) return;
        if (chipType === 'author') {
            state.modalAuthors.splice(chipIndex, 1);
        } else {
            state.modalTags.splice(chipIndex, 1);
        }
        syncChipPayloads();
    });

    elements.removeAttachmentBtn?.addEventListener('click', () => {
        state.removeAttachment = true;
        elements.removeAttachmentInput.value = 'true';
        renderExistingAttachment(null);
    });

    elements.attachmentInput?.addEventListener('change', () => {
        if (elements.attachmentInput.files?.length) {
            state.removeAttachment = false;
            elements.removeAttachmentInput.value = 'false';
            const existing = state.textbooks.find((item) => item.id === state.editingTextbookId);
            renderExistingAttachment(existing);
        }
    });

    elements.form?.addEventListener('submit', handleSubmit);

    // Intro/Catalog popup events
    elements.openIntroCatalogBtn?.addEventListener('click', openIntroCatalogPopup);
    elements.reformatBtn?.addEventListener('click', openIntroCatalogPopup);
    elements.introCatalogCloseBtn?.addEventListener('click', closeIntroCatalogPopup);
    elements.introCatalogCancelBtn?.addEventListener('click', closeIntroCatalogPopup);
    elements.introCatalogBackdrop?.addEventListener('click', (event) => {
        if (event.target === elements.introCatalogBackdrop) closeIntroCatalogPopup();
    });
    elements.introCatalogConfirmBtn?.addEventListener('click', handleAiFormatIntroCatalog);

    const params = new URLSearchParams(window.location.search);
    if (params.get('open') === 'new') {
        openModal('create');
    }
}

initEvents();
