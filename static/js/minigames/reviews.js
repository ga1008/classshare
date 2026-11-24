import {
    api,
    apiBase,
    bindIconTooltips,
    bootstrap,
    button,
    confirmAction,
    el,
    field,
    formPanel,
    gameIcon,
    modeText,
    notice,
    onAction,
    panel,
    run,
    showControl,
    statusChip,
    svgIcon,
} from './ui.js';
import { createMaterialsExplorer } from '../materials_explorer.js';
import { escapeHtml, formatSize } from '../ui.js';

const boot = bootstrap();
const base = '/moderation/reviews';
const filterForm = document.querySelector('[data-review-filter]');
const tabsRoot = document.querySelector('[data-review-tabs]');
const listRoot = document.querySelector('[data-review-list]');
const stageRoot = document.querySelector('[data-review-stage]');
const autoAiToggle = document.querySelector('[data-auto-ai-toggle]');
const queueHeading = document.querySelector('[data-queue-heading]');
const queueTotal = document.querySelector('[data-queue-total]');

const statusLabels = {
    manual: '待人工审核',
    ai_pending: 'AI 审核中',
    queued: '排队 AI 审核',
    running: 'AI 查验中',
    approved: '已通过',
    rejected: '已退回',
    cancelled: '已取消',
    all: '全部记录',
};
const statusTones = {
    manual: 'warning',
    queued: 'info',
    running: 'info',
    approved: 'success',
    rejected: 'danger',
    cancelled: 'neutral',
};
const categoryNames = {
    sexual: '重大色情风险',
    political: '重大涉政风险',
    drugs: '重大毒品风险',
    severe_harm: '重大违法与极端危害',
};
const categoryStateText = {
    clear: '正常',
    major_risk: '重大风险',
    violation: '重大风险',
    uncertain: '不确定',
};
const categoryStateTone = {
    clear: 'success',
    major_risk: 'danger',
    violation: 'danger',
    uncertain: 'warning',
};

const initialParams = new URLSearchParams(location.search);
let activeStatus = initialParams.get('status') || 'manual';
if (!statusLabels[activeStatus]) activeStatus = 'manual';
let searchQuery = initialParams.get('q') || '';
let selectedVersionId = initialParams.get('version') || '';
const offsets = [0];
let nextOffset = 0;
let listItems = [];
let activeDetail = null;
let listRequestSeq = 0;
let detailRequestSeq = 0;
let pollTimer = 0;
let activeExplorer = null;
let explorerVersionId = '';
let explorerExpanded = false;
let mobileTreeOpen = false;

if (filterForm?.elements?.q) {
    filterForm.elements.q.value = searchQuery;
}

function authorLabel(item) {
    const role = {teacher: '教师', student: '学生'}[item.ownerRole];
    return (role ? role + '作者 ' : '作者 ') + item.authorName;
}

function formatTimestamp(ms) {
    if (!ms) return '';
    try {
        return new Date(Number(ms)).toLocaleString('zh-CN', {
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit',
        });
    } catch {
        return '';
    }
}

function linkButton(label, href, variant = 'glass', opts = {}) {
    const a = el('a', null, 'lq-btn lq-btn--' + variant + ' lq-btn--' + (opts.size || 'sm'));
    a.dataset.lqComponent = 'button';
    a.href = href;
    if (opts.blank) {
        a.target = '_blank';
        a.rel = 'noopener noreferrer';
    }
    if (opts.icon) {
        const iconWrap = el('span', null, 'lq-btn__icon');
        iconWrap.setAttribute('aria-hidden', 'true');
        iconWrap.append(svgIcon(opts.icon));
        a.append(iconWrap);
    }
    a.append(el('span', label, 'lq-btn__label'));
    return a;
}

function syncUrl() {
    const u = new URL(location.href);
    if (activeStatus && activeStatus !== 'manual') u.searchParams.set('status', activeStatus);
    else u.searchParams.delete('status');
    if (searchQuery) u.searchParams.set('q', searchQuery);
    else u.searchParams.delete('q');
    if (selectedVersionId) u.searchParams.set('version', selectedVersionId);
    else u.searchParams.delete('version');
    history.replaceState(null, '', u);
}

function syncTabs(counts = {}) {
    if (queueHeading) queueHeading.textContent = statusLabels[activeStatus] || '审核队列';
    tabsRoot?.querySelectorAll('[data-review-status]').forEach((btn) => {
        const current = btn.dataset.reviewStatus === activeStatus;
        btn.setAttribute('aria-selected', String(current));
        btn.setAttribute('aria-pressed', String(current));
    });
    for (const [key, value] of Object.entries(counts)) {
        const badge = tabsRoot?.querySelector(`[data-count-for="${key}"]`);
        if (badge) badge.textContent = String(value ?? 0);
    }
}

function aiVerdictBadge(item) {
    const verdict = item?.aiVerdict || item?.aiReport?.verdict || '';
    if (item?.status === 'queued' || item?.status === 'running') {
        return statusChip(statusLabels[item.status], 'info');
    }
    if (verdict === 'major_risk') return statusChip('AI 预警：重大风险', 'danger');
    if (verdict === 'uncertain') return statusChip('AI 需人工确认', 'warning');
    if (verdict === 'error') return statusChip('AI 未完成', 'warning');
    if (verdict === 'approved' || (item?.status === 'approved' && item?.decider === 'ai')) {
        return statusChip('AI 已查验通过', 'success');
    }
    return null;
}

function renderQueue() {
    if (queueTotal) queueTotal.textContent = listItems.length + ' 项';
    if (!listItems.length) {
        const empty = el('li', null, 'game-review-empty');
        empty.append(
            el('strong', activeStatus === 'manual' ? '当前没有等待人工审核的小游戏' : '当前筛选下暂无记录'),
            el(
                'p',
                activeStatus === 'manual'
                    ? '教师和学生的作品通过基础校验后，在此等候超级管理员审核。'
                    : '可以切换审核状态或清空搜索词查看其他版本。',
                'game-muted'
            )
        );
        listRoot.replaceChildren(empty);
        return;
    }

    const rows = listItems.map((item) => {
        const li = el('li', null, 'game-review-item');
        const selected = item.versionId === selectedVersionId;
        if (selected) li.dataset.lqSelected = 'true';

        const btn = el('button', null, 'lq-btn lq-btn--ghost lq-domain-choice game-review-card');
        btn.type = 'button';
        btn.dataset.lqComponent = 'choice';
        btn.dataset.lqShape = 'surface';
        btn.setAttribute('aria-pressed', String(selected));
        btn.addEventListener('click', () => {
            if (selectedVersionId === item.versionId && activeDetail?.versionId === item.versionId) return;
            selectedVersionId = item.versionId;
            syncUrl();
            renderQueue();
            run(null, () => loadDetail(item.versionId));
        });

        const top = el('div', null, 'game-review-card__top');
        const iconNode = gameIcon({ id: item.gameId, title: item.title, iconUrl: item.iconUrl });
        const copy = el('div', null, 'game-review-card__copy');
        const titleRow = el('div', null, 'game-row-head');
        titleRow.append(el('strong', item.title || '未命名作品'));
        if (item.version) titleRow.append(el('code', 'v' + item.version, 'game-key-pill'));
        const metaRow = el('div', null, 'game-row-meta');
        if (item.authorName) metaRow.append(el('small', authorLabel(item)));
        if (item.fileCount != null) metaRow.append(el('small', item.fileCount + ' 个文件'));
        const ts = formatTimestamp(item.updatedAt || item.createdAt);
        if (ts) metaRow.append(el('small', ts));
        copy.append(titleRow, metaRow);
        top.append(iconNode, copy);

        const badges = el('div', null, 'game-row-head game-review-card__badges');
        badges.append(statusChip(statusLabels[item.status] || item.status, statusTones[item.status] || 'neutral'));
        const verdictChip = aiVerdictBadge(item);
        if (verdictChip) badges.append(verdictChip);

        btn.append(top, badges);
        if (item.reason) {
            btn.append(el('p', item.reason, 'game-review-card__reason game-muted'));
        }
        li.append(btn);
        return li;
    });
    listRoot.replaceChildren(...rows);
}

function renderEmptyStage(message, detailText) {
    activeExplorer?.close?.();
    activeExplorer = null;
    explorerVersionId = '';
    const card = el('section', null, 'lq-surface game-section game-review-placeholder');
    card.dataset.lqComponent = 'surface';
    card.append(
        el('h2', message || '选择一个待审版本开始核查', 'game-section-title'),
        el(
            'p',
            detailText ||
                '教师和学生提交的版本都需要超级管理员审核。可以浏览完整游戏包、隔离试玩，也可以参考 AI 的风险报告。',
            'game-muted'
        )
    );
    const steps = el('ol', null, 'game-review-flow');
    for (const [title, desc] of [
        ['1. 后端第一轮逻辑校验', '自动检查 config.json、main.html、协议声明、素材格式与安全限制，不合规范时提示作者修复。'],
        ['2. 核对内容与试玩体验', '树形浏览已解压源码与图片/音视频，或提交 AI 仅核查重大内容风险（允许正常游戏的少量成人元素）。'],
        ['3. 正式发布或退回修改', '超级管理员核对材料后决定发布或退回，并将修改理由反馈给作者。'],
    ]) {
        const li = el('li', null, 'lq-surface game-review-flow__item');
        li.dataset.lqComponent = 'surface';
        li.dataset.lqSurface = 'embedded';
        li.append(el('strong', title), el('p', desc, 'game-muted'));
        steps.append(li);
    }
    card.append(steps);
    stageRoot.replaceChildren(card);
}

function renderReportCard(item) {
    const report = item.aiReport;
    const isAiRunning = item.status === 'queued' || item.status === 'running';
    const hasEvidence = Array.isArray(item.evidence) && item.evidence.length > 0;
    const hasHistory = Boolean(report || item.aiReason || hasEvidence || item.status === 'approved' || item.status === 'rejected');
    if (!isAiRunning && !hasHistory) return null;

    const card = el('section', null, 'lq-surface game-section game-review-report');
    card.dataset.lqComponent = 'surface';
    card.dataset.reviewReport = '';

    const head = el('div', null, 'game-section-head');
    const title = el('h3', null, 'game-section-title');
    title.append(svgIcon('sparkles', 'game-mini-icon'), el('span', isAiRunning ? 'AI 风险查验进度' : '审核结论与 AI 查验报告'));
    head.append(title);
    if (report?.checkedAt) {
        head.append(el('small', '查验时间 ' + formatTimestamp(report.checkedAt), 'game-muted'));
    }
    card.append(head);

    if (isAiRunning) {
        const alert = el('div', null, 'lq-alert game-status-row');
        alert.dataset.lqComponent = 'alert';
        alert.dataset.lqStatus = 'alert';
        alert.dataset.lqToneLevel = 'info';
        const row = el('div', null, 'game-row-head');
        row.append(
            statusChip(statusLabels[item.status] || item.status, 'info'),
            el('span', item.reason || 'AI 正在检查代码、文案与图像，完成后将生成报告，供超级管理员审核。')
        );
        alert.append(row);
        card.append(alert);
        return card;
    }

    const verdict = report?.verdict || '';
    let bannerTone = 'info';
    let bannerLabel = statusLabels[item.status] || item.status;
    let bannerText = item.reason || '';

    if (item.status === 'approved') {
        bannerTone = 'success';
        bannerLabel = item.decider === 'ai' ? 'AI 查验通过并已发布' : '超管审核通过并已发布';
    } else if (item.status === 'rejected') {
        bannerTone = 'danger';
        bannerLabel = '已退回作者';
    } else if (verdict === 'major_risk') {
        bannerTone = 'danger';
        bannerLabel = 'AI 预警：发现重大内容风险';
    } else if (verdict === 'uncertain') {
        bannerTone = 'warning';
        bannerLabel = 'AI 需人工确认';
    } else if (verdict === 'error') {
        bannerTone = 'warning';
        bannerLabel = 'AI 审核未完成';
    }

    const alert = el('div', null, 'lq-alert game-status-row');
    alert.dataset.lqComponent = 'alert';
    alert.dataset.lqStatus = 'alert';
    alert.dataset.lqToneLevel = bannerTone;
    const alertMain = el('div', null, 'game-row-head');
    alertMain.append(statusChip(bannerLabel, bannerTone));
    if (bannerText) alertMain.append(el('span', bannerText));
    alert.append(alertMain);
    card.append(alert);

    if (report) {
        const metaRow = el('div', null, 'game-row-meta game-review-report__dims');
        const states = report.categoryStates || {};
        for (const [key, stateVal] of Object.entries(states)) {
            metaRow.append(
                statusChip(
                    (categoryNames[key] || key) + ' · ' + (categoryStateText[stateVal] || stateVal),
                    categoryStateTone[stateVal] || 'neutral'
                )
            );
        }
        if (report.totalCount > 0) {
            const covText =
                '覆盖率 ' +
                report.checkedCount +
                ' / ' +
                report.totalCount +
                (report.unreadCount > 0 ? '（' + report.unreadCount + ' 项需人工预览/试听）' : '');
            metaRow.append(el('span', covText, 'game-key-pill'));
        }
        if (metaRow.childElementCount) card.append(metaRow);

        const dl = el('dl', null, 'game-attr-list game-review-report__dl');
        const addRow = (label, text) => {
            if (!text) return;
            const row = el('div', null, 'game-attr-item');
            row.append(el('dt', label), el('dd', text));
            dl.append(row);
        };
        addRow('AI 查验报告', report.summary);
        if (report.adultElementsNote && report.adultElementsNote !== '未发现') {
            addRow('成人/成熟元素评估', report.adultElementsNote);
        } else if (report.adultElementsNote) {
            addRow('成人/成熟元素评估', report.adultElementsNote);
        }
        addRow('人工复核建议', report.reviewAdvice);
        if (item.aiReason && item.aiReason !== item.reason && item.aiReason !== report.summary) {
            addRow('AI 审核依据', item.aiReason);
        }
        if (dl.childElementCount) card.append(dl);
    } else if (item.aiReason && item.aiReason !== item.reason) {
        card.append(el('p', 'AI 查验说明：' + item.aiReason, 'game-muted'));
    }

    if (item.categories?.length && !report?.categoryStates) {
        const cats = el('div', null, 'game-row-meta');
        for (const c of item.categories) {
            cats.append(statusChip(categoryNames[c] || c, 'danger'));
        }
        card.append(cats);
    }

    if (hasEvidence) {
        const box = el('div', null, 'game-review-evidence');
        box.append(el('h4', 'AI 标记的重点文件与核查依据（点击文件可直接定位预览）', 'game-subhead'));
        const ul = el('ul', null, 'game-review-evidence__list');
        for (const finding of item.evidence) {
            const li = el('li', null, 'game-review-evidence__item');
            const filePath = finding.file || '';
            if (filePath && filePath !== '上传资料') {
                const jump = button(filePath, () => jumpToPackageFile(filePath), 'soft', { icon: 'eye' });
                jump.classList.add('game-review-evidence__jump');
                li.append(jump);
            } else if (filePath) {
                li.append(el('code', filePath, 'game-key-pill'));
            }
            if (finding.reason) li.append(el('span', finding.reason));
            ul.append(li);
        }
        box.append(ul);
        card.append(box);
    }

    return card;
}

async function jumpToPackageFile(relPath) {
    if (!activeExplorer || !relPath) return;
    const jumped = await activeExplorer.selectByPath(relPath);
    const host = stageRoot.querySelector('[data-review-explorer-card]');
    host?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    if (!jumped) notice('未在文件树中找到 ' + relPath);
}

async function openIsolatedPreview(item) {
    const result = await api(base + '/' + encodeURIComponent(item.versionId) + '/preview', { method: 'POST' });
    const u = new URL(result.url, location.origin);
    if (u.origin !== location.origin) throw new Error('无效的预览地址');
    const body = el('div', null, 'game-form');
    body.append(
        el('p', '将在独立无 Cookie 沙盒域名中运行此待审版本，便于测试实际玩法与画面。', 'game-muted'),
        linkButton('打开隔离试玩窗口', u.href, 'prominent', { icon: 'external', blank: true, size: 'md' })
    );
    await panel('隔离试玩预览', body);
}

async function triggerAiReview(item) {
    const label = item.aiReport ? '重新提交 AI 审核' : '提交 AI 审核';
    const msg =
        'AI 将检查代码、文案和图片，标出风险与未覆盖的素材。完成后请核对报告并决定是否发布。';
    if (!(await confirmAction(label, msg))) return;
    const updated = await api(base + '/' + encodeURIComponent(item.versionId) + '/ai-review', {
        method: 'POST',
        body: { expectedDigest: item.digest },
    });
    activeDetail = updated;
    notice('已提交 AI 风险查验，处理完成后将自动更新状态');
    renderDetail(updated);
    await loadList({ keepDetail: true });
}

async function submitDecision(item, decision) {
    const approve = decision === 'approve';
    const label = approve ? '审核通过并发布' : '退回作者';
    const body = el('div', null, 'game-form');
    body.append(
        el(
            'p',
            approve
                ? '审核通过后，版本 ' + (item.version || '') + ' 将立即正式发布到小游戏大厅。'
                : '退回后，作者可按意见修改并上传新版本。',
            'game-muted'
        )
    );
    const reasonField = field(approve ? '审核意见（通过备注）' : '退回理由（将反馈给作者）', 'reason');
    const input = reasonField.querySelector('input');
    input.required = true;
    input.maxLength = 400;
    if (approve) input.value = '人工审核通过，准予正式发布';
    else input.placeholder = '请填写具体问题及修改建议…';
    body.append(reasonField);

    await formPanel(
        label,
        body,
        approve ? '确认通过并发布' : '确认退回作者',
        async (data) => {
            const reason = String(data.get('reason') || '').trim();
            if (!reason) throw new Error('请填写审核意见');
            const result = await api(base + '/' + encodeURIComponent(item.versionId) + '/decision', {
                method: 'POST',
                body: { decision, reason, expectedDigest: item.digest },
            });
            activeDetail = result;
            notice(approve ? '已审核通过并正式发布' : '已退回作者');
            renderDetail(result);
            await loadList({ keepDetail: true });
        },
        { danger: !approve }
    );
}

function mountPackageExplorer(container, item) {
    const vid = item.versionId;
    if (activeExplorer && explorerVersionId === vid && container.contains(activeExplorer._rootEl)) {
        return;
    }
    activeExplorer?.close?.();
    activeExplorer = null;
    explorerVersionId = vid;

    const treeUrl = item.treeUrl || apiBase + base + '/' + encodeURIComponent(vid) + '/tree';
    const explorer = createMaterialsExplorer({
        mount: container,
        mode: 'embedded',
        readOnly: true,
        defaultDetailsOpen: false,
        storageKeyPrefix: 'lanshare:minigames:review-explorer',
        searchPlaceholder: '在游戏包内按文件名筛选…',
        rootLabel: item.title || '游戏包',
        loadTree: async () => api(treeUrl.replace(apiBase, '')),
        loadDetail: async (_id, node) => node,
        fetchContent: async (node) => {
            if (!node?.preview_content_url) return null;
            const path = node.preview_content_url.startsWith(apiBase)
                ? node.preview_content_url.slice(apiBase.length)
                : node.preview_content_url;
            return api(path);
        },
        renderActions: () =>
            item.packageUrl
                ? `<a data-lq-component="button" class="lq-btn lq-btn--sm lq-btn--glass" href="${escapeHtml(item.packageUrl)}">下载完整 ZIP</a>`
                : '',
        renderExtraDetails: (detail) => {
            const notes = Array.isArray(detail?.ai_findings) ? detail.ai_findings.filter(Boolean) : [];
            if (!notes.length) return '';
            return `<div class="mx__summary game-review-mx-findings"><span>AI 重点复核提示</span>${notes.map((n) => `<p>${escapeHtml(n)}</p>`).join('')}</div>`;
        },
    });
    activeExplorer = explorer;
    void explorer.open(vid).then(() => {
        explorer._rootEl = container.firstElementChild;
        if (mobileTreeOpen) container.dataset.mobileTree = 'open';
    });
}

function renderDetail(item) {
    if (!item) {
        renderEmptyStage();
        return;
    }
    const fragment = document.createDocumentFragment();

    // 1. Header + metadata + decision actions
    const hero = el('section', null, 'lq-surface game-detail-hero game-review-hero');
    hero.dataset.lqComponent = 'surface';

    const head = el('div', null, 'game-detail-head');
    head.append(gameIcon({ id: item.gameId, title: item.title, iconUrl: item.iconUrl }, { eager: true }));
    const titleBox = el('div', null, 'game-detail-title');
    const h2 = el('h2', item.title || '未命名作品', 'game-review-title');
    const statusRow = el('div', null, 'game-row-head');
    statusRow.append(statusChip(statusLabels[item.status] || item.status, statusTones[item.status] || 'neutral'));
    const verdictChip = aiVerdictBadge(item);
    if (verdictChip && item.status === 'manual') statusRow.append(verdictChip);
    if (item.version) statusRow.append(el('code', 'v' + item.version, 'game-key-pill'));
    if (item.gameKey) statusRow.append(el('code', item.gameKey, 'game-key-pill'));
    titleBox.append(h2, statusRow);
    head.append(titleBox);
    hero.append(head);

    if (item.summary) {
        hero.append(el('p', item.summary, 'game-review-summary'));
    }

    const meta = el('div', null, 'game-row-meta game-review-meta');
    if (item.authorName) meta.append(el('small', authorLabel(item)));
    if (item.sourceAuthor) meta.append(el('small', '改编自：' + item.sourceAuthor));
    const genre = (boot.genreOptions || []).find((g) => g.id === item.config?.genre);
    if (genre) meta.append(el('span', genre.label, 'game-tag-pill'));
    const modes = modeText({ config: item.config });
    if (modes) meta.append(el('small', modes));
    meta.append(el('small', (item.fileCount ?? (item.files || []).length) + ' 个文件'));
    if (item.totalBytes) meta.append(el('small', formatSize(item.totalBytes)));
    if (item.allowRemix) meta.append(el('span', '允许二开', 'game-policy-pill'));
    for (const tag of (item.tags || []).slice(0, 8)) {
        meta.append(el('span', '#' + tag, 'game-tag-pill'));
    }
    if (item.digest) meta.append(el('code', 'SHA ' + item.digest.slice(0, 10) + '…', 'game-key-pill'));
    hero.append(meta);

    const quickbar = el('div', null, 'game-quickbar');
    quickbar.setAttribute('role', 'toolbar');
    quickbar.setAttribute('aria-label', '审核操作');
    const mainActions = el('div', null, 'game-quickbar-main');
    const dangerActions = el('div', null, 'game-quickbar-danger');

    const canDecide = ['manual', 'queued', 'running'].includes(item.status) && item.versionStatus !== 'deleted';
    if (canDecide) {
        mainActions.append(button('审核通过并发布', () => submitDecision(item, 'approve'), 'prominent', { icon: 'check' }));
    }
    if (item.status === 'manual' && item.versionStatus !== 'deleted') {
        mainActions.append(
            button(item.aiReport ? '重新 AI 查验' : '提交 AI 审核', () => triggerAiReview(item), 'soft', {
                icon: 'sparkles',
            })
        );
    }
    if (item.packageUrl) {
        mainActions.append(linkButton('下载完整文件包', item.packageUrl, 'glass', { icon: 'download' }));
    }
    if (item.canPreview) {
        mainActions.append(button('隔离试玩', () => openIsolatedPreview(item), 'glass', { icon: 'eye' }));
    }
    if (item.manageUrl) {
        mainActions.append(linkButton('作品详情', item.manageUrl, 'ghost', { icon: 'external' }));
    }
    if (canDecide) {
        dangerActions.append(button('退回作者', () => submitDecision(item, 'reject'), 'destructive', { icon: 'x' }));
    }

    quickbar.append(mainActions);
    if (dangerActions.childElementCount) quickbar.append(dangerActions);
    hero.append(quickbar);
    fragment.append(hero);

    // 2. AI inspection report & risk evidence
    const reportCard = renderReportCard(item);
    if (reportCard) fragment.append(reportCard);

    // 3. Embedded Material Explorer for extracted package files
    const explorerCard = el('section', null, 'lq-surface game-section game-review-explorer-card');
    explorerCard.dataset.lqComponent = 'surface';
    explorerCard.dataset.reviewExplorerCard = '';
    if (explorerExpanded) explorerCard.dataset.expanded = 'true';

    const expHead = el('div', null, 'game-section-head');
    const expTitle = el('h3', null, 'game-section-title');
    expTitle.append(
        svgIcon('layers', 'game-mini-icon'),
        el('span', '文件包在线浏览（已解压）'),
        el('small', (item.fileCount ?? (item.files || []).length) + ' 个文件', 'game-key-pill')
    );
    const expTools = el('div', null, 'game-actions');
    const mobileTreeBtn = button(
        mobileTreeOpen ? '收起目录树' : '目录树',
        () => {
            mobileTreeOpen = !mobileTreeOpen;
            explorerHost.dataset.mobileTree = mobileTreeOpen ? 'open' : 'closed';
            const lbl = mobileTreeBtn.querySelector('.lq-btn__label');
            if (lbl) lbl.textContent = mobileTreeOpen ? '收起目录树' : '目录树';
        },
        'ghost',
        { icon: 'layers' }
    );
    mobileTreeBtn.classList.add('game-review-tree-toggle');
    const expandBtn = button(
        explorerExpanded ? '还原窗口' : '放大浏览',
        () => {
            explorerExpanded = !explorerExpanded;
            explorerCard.dataset.expanded = explorerExpanded ? 'true' : 'false';
            const lbl = expandBtn.querySelector('.lq-btn__label');
            if (lbl) lbl.textContent = explorerExpanded ? '还原窗口' : '放大浏览';
        },
        'ghost',
        { icon: 'external' }
    );
    expTools.append(mobileTreeBtn, expandBtn);
    expHead.append(expTitle, expTools);

    const explorerHost = el('div', null, 'game-review-explorer');
    explorerHost.dataset.reviewExplorer = '';
    explorerHost.dataset.mobileTree = mobileTreeOpen ? 'open' : 'closed';

    explorerCard.append(expHead, explorerHost);
    fragment.append(explorerCard);

    stageRoot.replaceChildren(fragment);
    mountPackageExplorer(explorerHost, item);
    bindIconTooltips(stageRoot);
}

function schedulePoll() {
    clearTimeout(pollTimer);
    if (document.hidden) return;
    const anyAiActive =
        activeStatus === 'ai_pending' ||
        listItems.some((item) => item.status === 'queued' || item.status === 'running') ||
        (activeDetail && (activeDetail.status === 'queued' || activeDetail.status === 'running'));
    if (anyAiActive) {
        pollTimer = setTimeout(() => {
            run(null, () => loadList({ keepDetail: true, silentRefresh: true }));
        }, 3000);
    }
}

async function loadDetail(vid, { forceExplorer = false } = {}) {
    if (!vid) {
        activeDetail = null;
        renderEmptyStage();
        return;
    }
    const seq = ++detailRequestSeq;
    const data = await api(base + '/' + encodeURIComponent(vid));
    if (seq !== detailRequestSeq) return;
    const prevVid = activeDetail?.versionId;
    const prevDigest = activeDetail?.digest;
    const prevStatus = activeDetail?.status;
    activeDetail = data;
    if (forceExplorer || prevVid !== data.versionId || prevDigest !== data.digest) {
        explorerVersionId = '';
    }
    renderDetail(data);
    if (prevVid === data.versionId && prevStatus && prevStatus !== data.status) {
        if (data.status === 'approved') notice('该版本已审核通过并发布');
        else if (data.status === 'manual') notice('AI 查验完成，请人工查看查验报告');
    }
    schedulePoll();
}

async function loadList({ keepDetail = false, silentRefresh = false } = {}) {
    const seq = ++listRequestSeq;
    const params = new URLSearchParams({ status: activeStatus, offset: String(offsets.at(-1)) });
    if (searchQuery) params.set('q', searchQuery);
    const result = await api(base + '?' + params);
    if (seq !== listRequestSeq) return;
    listItems = result.items || [];
    syncTabs(result.counts || {});
    if (autoAiToggle && result.settings) {
        autoAiToggle.checked = Boolean(result.settings.autoAiReview);
    }
    nextOffset = offsets.at(-1) + listItems.length;
    showControl('next', Boolean(result.hasMore && listItems.length > 0));
    showControl('previous', offsets.length > 1);

    const hasSelectedInList = selectedVersionId && listItems.some((item) => item.versionId === selectedVersionId);
    if (!selectedVersionId && listItems.length) {
        selectedVersionId = listItems[0].versionId;
    } else if (selectedVersionId && !hasSelectedInList && !keepDetail && listItems.length) {
        // Keep deep-linked version only on initial load; switching tabs selects the first row in the new tab.
        if (!silentRefresh) selectedVersionId = listItems[0].versionId;
    }
    syncUrl();
    renderQueue();

    if (selectedVersionId) {
        await loadDetail(selectedVersionId);
    } else {
        activeDetail = null;
        renderEmptyStage();
    }
    schedulePoll();
}

tabsRoot?.addEventListener('click', (event) => {
    const btn = event.target.closest('[data-review-status]');
    if (!btn) return;
    const next = btn.dataset.reviewStatus;
    if (!next || next === activeStatus) return;
    activeStatus = next;
    selectedVersionId = '';
    offsets.splice(0, offsets.length, 0);
    run(btn, () => loadList());
});

filterForm?.addEventListener('submit', (event) => {
    event.preventDefault();
    searchQuery = String(filterForm.elements.q?.value || '').trim();
    selectedVersionId = '';
    offsets.splice(0, offsets.length, 0);
    run(event.submitter, () => loadList());
});

autoAiToggle?.addEventListener('change', () => {
    const next = Boolean(autoAiToggle.checked);
    run(autoAiToggle, async () => {
        try {
            const res = await api(base + '/settings', {
                method: 'PATCH',
                body: { autoAiReview: next },
            });
            autoAiToggle.checked = Boolean(res.autoAiReview);
            notice(
                res.autoAiReview
                    ? '已开启自动 AI 审核：新提交通过基础校验后将自动送 AI 查验'
                    : '已关闭自动 AI 审核：新提交通过基础校验后将直接进入人工审核队列'
            );
        } catch (err) {
            autoAiToggle.checked = !next;
            throw err;
        }
    });
});

onAction({
    refresh: () => loadList({ keepDetail: Boolean(selectedVersionId) }),
    next: () => {
        offsets.push(nextOffset);
        return loadList();
    },
    previous: () => {
        if (offsets.length > 1) offsets.pop();
        return loadList();
    },
});

document.addEventListener('visibilitychange', () => {
    clearTimeout(pollTimer);
    if (!document.hidden) schedulePoll();
});

run(null, () => loadList({ keepDetail: Boolean(selectedVersionId) }));
bindIconTooltips(document);
