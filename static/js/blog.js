import { createEmojiPicker } from '/static/js/emoji_picker.js';
import { escapeHtml, showToast } from '/static/js/ui.js';

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const POSTS_PAGE_SIZE = 20;

const ROLE_LABELS = {
    teacher: '教师',
    student: '学生',
    assistant: 'AI助教',
};

const OPPORTUNITY_TYPE_LABELS = {
    campus_recruitment: '校园招聘',
    internship: '实习机会',
    public_institution: '事业单位',
    civil_service: '公务员招录',
    grassroots_program: '基层项目',
    career_fair: '招聘会',
    policy: '就业政策',
    other: '就业机会',
};

const OPPORTUNITY_STATE_LABELS = {
    saved: '已收藏',
    preparing: '准备材料',
    applied: '已投递',
    interview: '笔试/面试',
    offer: '已获录用',
    closed: '已结束',
};

function normalizeCareerFilters(filters = {}) {
    const region = String(filters.region || '').trim().toLowerCase();
    const opportunityType = String(filters.opportunityType || '').trim().toLowerCase();
    const deadlineDays = String(filters.deadlineDays || '').trim();
    const userState = String(filters.userState || '').trim().toLowerCase();
    return {
        region: ['nanning', 'guangxi', 'prd'].includes(region) ? region : '',
        opportunityType: Object.hasOwn(OPPORTUNITY_TYPE_LABELS, opportunityType) ? opportunityType : '',
        deadlineDays: ['7', '30', '60'].includes(deadlineDays) ? deadlineDays : '',
        userState: Object.hasOwn(OPPORTUNITY_STATE_LABELS, userState) ? userState : '',
    };
}

const SVG = {
    eye: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>',
    heart: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>',
    heartFill: '<svg viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>',
    comment: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>',
    bookmark: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/></svg>',
    bookmarkFill: '<svg viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/></svg>',
    image: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="m21 15-5-5L5 21"/></svg>',
    plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 5v14"/><path d="M5 12h14"/></svg>',
    edit: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 1 1 3 3L7 19l-4 1 1-4Z"/></svg>',
    smile: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M8 15s1.5 2 4 2 4-2 4-2"/><path d="M9 9h.01"/><path d="M15 9h.01"/></svg>',
};

const api = {
    async request(url, options = {}) {
        const response = await fetch(url, options);
        let payload = {};
        try {
            payload = await response.json();
        } catch (error) {
            payload = {};
        }
        if (!response.ok) {
            const detail = payload?.detail || payload?.message || '请求失败';
            throw new Error(detail);
        }
        return payload;
    },
    get(url, options = {}) {
        return this.request(url, options);
    },
    post(url, body) {
        return this.request(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body || {}),
        });
    },
    put(url, body) {
        return this.request(url, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body || {}),
        });
    },
    del(url) {
        return this.request(url, { method: 'DELETE' });
    },
    async upload(url, formData) {
        const response = await fetch(url, { method: 'POST', body: formData });
        let payload = {};
        try {
            payload = await response.json();
        } catch (error) {
            payload = {};
        }
        if (!response.ok) {
            throw new Error(payload?.detail || payload?.message || '上传失败');
        }
        return payload;
    },
};

function renderMarkdownHtml(markdown) {
    if (typeof MarkdownRuntime !== 'undefined' && typeof MarkdownRuntime.parse === 'function') {
        try {
            return MarkdownRuntime.parse(markdown || '', { fallbackMode: 'pre-code' });
        } catch (error) {
            console.error('Markdown render failed', error);
        }
    }
    return escapeHtml(markdown || '').replace(/\n/g, '<br>');
}

function timeAgo(value) {
    if (!value) return '';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '';
    const diffSeconds = Math.max(1, Math.floor((Date.now() - date.getTime()) / 1000));
    if (diffSeconds < 60) return '刚刚';
    const diffMinutes = Math.floor(diffSeconds / 60);
    if (diffMinutes < 60) return `${diffMinutes} 分钟前`;
    const diffHours = Math.floor(diffMinutes / 60);
    if (diffHours < 24) return `${diffHours} 小时前`;
    const diffDays = Math.floor(diffHours / 24);
    if (diffDays < 30) return `${diffDays} 天前`;
    const diffMonths = Math.floor(diffDays / 30);
    if (diffMonths < 12) return `${diffMonths} 个月前`;
    return `${Math.floor(diffMonths / 12)} 年前`;
}

function formatCompactNumber(value) {
    const number = Number(value || 0);
    if (number >= 10000) return `${(number / 10000).toFixed(number >= 100000 ? 0 : 1)}万`;
    if (number >= 1000) return `${(number / 1000).toFixed(number >= 10000 ? 0 : 1)}k`;
    return String(number);
}

function normalizeFileHash(item) {
    return String(item?.file_hash || item?.hash || '').trim().toLowerCase();
}

function uniqueMediaItems(items) {
    const seen = new Set();
    return (items || []).filter((item) => {
        const fileHash = normalizeFileHash(item);
        if (!fileHash || seen.has(fileHash)) return false;
        seen.add(fileHash);
        return true;
    });
}

class BlogCenter {
    constructor(shell) {
        this.shell = shell;
        this.userIdentity = shell.dataset.currentUserIdentity || '';
        this.userRole = (shell.dataset.currentUserRole || '').trim().toLowerCase();
        this.userName = shell.dataset.currentUserName || '';
        this.userNickname = shell.dataset.currentUserNickname || '';
        this.initialSection = (shell.dataset.initialSection || '').trim().toLowerCase();
        this.currentAvatarUrl = '/api/profile/avatar';
        this.composeUserMap = new Map();
        this.commentDraft = this.createEmptyCommentDraft();
        this.searchTimer = null;
        this.userSearchTimer = null;
        this.composeAutosaveTimer = null;
        this.composeBaseline = '';
        this.composerReturnFocus = null;
        this.detailHasListHistory = false;
        this.detailPreviousIsPost = false;
        this.lastListSnapshot = null;
        this.requestControllers = new Map();
        this.followKeys = new Set();
        this.reportTarget = null;
        this.readingSession = null;
        this.readingProgressTimer = null;
        this.readingScrollTimer = null;
        this.channelRevealTimer = null;
        this.tunerScrollFrame = null;
        this.tunerExpandedTop = null;

        this.state = {
            currentView: 'feed',
            currentNav: 'feed',
            currentSort: 'latest',
            currentSection: this.initialSection,
            sections: [],
            detailPostId: null,
            detailPost: null,
            posts: [],
            myPosts: [],
            bookmarkPosts: [],
            discovery: null,
            page: 1,
            myPage: 1,
            bmPage: 1,
            hasMore: false,
            myHasMore: false,
            bmHasMore: false,
            editingPostId: null,
            uploadedImages: [],
            selectedUsers: [],
            myPostsFilter: null,
            authorFilter: null,
            tagFilter: null,
            composeClassesLoaded: false,
            customEmojiLibrary: [],
            careerFilters: normalizeCareerFilters(),
        };
    }

    createEmptyCommentDraft() {
        return {
            replyTo: null,
            replyName: '',
            attachments: [],
            customEmojis: [],
            emojiPicker: null,
        };
    }

    beginRequest(key) {
        this.requestControllers.get(key)?.abort();
        const controller = new AbortController();
        this.requestControllers.set(key, controller);
        return controller;
    }

    buildListSnapshot() {
        return {
            section: this.state.currentSection || '',
            sort: this.state.currentSort || 'latest',
            nav: this.state.currentNav || 'feed',
            query: $('[data-blog-search]', this.shell)?.value?.trim() || '',
            tag: this.state.tagFilter || '',
            author: this.state.authorFilter || null,
            careerFilters: normalizeCareerFilters(this.state.careerFilters),
            page: this.state.page || 1,
            scrollY: Math.max(0, Math.round(window.scrollY || 0)),
        };
    }

    buildHistoryState(view, postId = null) {
        return {
            blog: {
                view,
                postId: postId || null,
                snapshot: view === 'list'
                    ? this.buildListSnapshot()
                    : (this.lastListSnapshot || this.buildListSnapshot()),
            },
        };
    }

    restoreListSnapshot(snapshot = {}) {
        this.state.currentSection = String(snapshot.section || '').trim().toLowerCase();
        this.state.currentSort = snapshot.sort || 'latest';
        this.state.currentNav = snapshot.nav || 'feed';
        this.state.tagFilter = snapshot.tag || null;
        this.state.authorFilter = snapshot.author || null;
        this.state.careerFilters = normalizeCareerFilters(snapshot.careerFilters);
        this.state.page = Math.max(1, Number(snapshot.page || 1));
        const searchInput = $('[data-blog-search]', this.shell);
        if (searchInput) searchInput.value = snapshot.query || '';
        this.updateNavTabs();
        this.updateSortTabs();
        this.renderSectionTabs();
        this.renderSectionIntro();
        this.updateCareerTools();
        this.updateAuthorFilterBanner();
        this.updateTagFilterBanner();
    }

    async handlePopState(event) {
        const url = new URL(window.location.href);
        const postId = Number(url.searchParams.get('post') || 0);
        if (postId) {
            this.detailHasListHistory = true;
            this.detailPreviousIsPost = event.state?.blog?.parentView === 'detail';
            await this.showDetail(postId, { historyMode: 'none', scrollToTop: false });
            return;
        }

        const snapshot = event.state?.blog?.snapshot || {
            section: url.searchParams.get('section') || '',
            sort: url.searchParams.get('sort') || 'latest',
            query: url.searchParams.get('q') || '',
            nav: url.searchParams.get('view') || 'feed',
            careerFilters: {
                region: url.searchParams.get('region') || '',
                opportunityType: url.searchParams.get('type') || '',
                deadlineDays: url.searchParams.get('deadline') || '',
                userState: url.searchParams.get('state') || '',
            },
            scrollY: 0,
        };
        this.detailHasListHistory = false;
        this.detailPreviousIsPost = false;
        this.state.detailPostId = null;
        this.state.detailPost = null;
        this.restoreListSnapshot(snapshot);
        this.showCurrentListView();
        if (this.state.currentNav === 'my-posts') {
            await this.loadMyPosts();
        } else if (this.state.currentNav === 'bookmarks') {
            await this.loadBookmarks();
        } else {
            await this.loadFeed();
        }
        this.loadDiscovery();
        window.requestAnimationFrame(() => window.scrollTo({ top: Number(snapshot.scrollY || 0), behavior: 'auto' }));
    }

    handleKeydown(event) {
        const sectionTab = event.target.closest?.('[data-blog-section][role="tab"]');
        if (sectionTab && ['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) {
            const tabs = $$('[data-blog-section][role="tab"]', this.shell);
            const currentIndex = tabs.indexOf(sectionTab);
            if (currentIndex < 0) return;
            event.preventDefault();
            let nextIndex = currentIndex;
            if (event.key === 'ArrowLeft') nextIndex = (currentIndex - 1 + tabs.length) % tabs.length;
            if (event.key === 'ArrowRight') nextIndex = (currentIndex + 1) % tabs.length;
            if (event.key === 'Home') nextIndex = 0;
            if (event.key === 'End') nextIndex = tabs.length - 1;
            tabs[nextIndex]?.focus();
            tabs[nextIndex]?.click();
            return;
        }

        const modal = $('[data-blog-composer-modal]', this.shell);
        if (!modal || modal.hidden || event.key !== 'Tab') return;
        const focusable = $$('button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])', modal)
            .filter((node) => !node.hidden && node.offsetParent !== null);
        if (!focusable.length) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
        }
    }

    init() {
        this.bindEvents();
        this.updateTunerCompactState();
        this.ensureCustomEmojiLibrary();
        this.loadFollows();

        const url = new URL(window.location.href);
        this.state.currentSection = (url.searchParams.get('section') || this.initialSection || '').trim().toLowerCase();
        this.state.currentSort = url.searchParams.get('sort') || 'latest';
        const requestedNav = (url.searchParams.get('view') || 'feed').trim().toLowerCase();
        this.state.currentNav = ['feed', 'following', 'my-posts', 'bookmarks'].includes(requestedNav)
            ? requestedNav
            : 'feed';
        this.state.careerFilters = normalizeCareerFilters({
            region: url.searchParams.get('region') || '',
            opportunityType: url.searchParams.get('type') || '',
            deadlineDays: url.searchParams.get('deadline') || '',
            userState: url.searchParams.get('state') || '',
        });
        const searchInput = $('[data-blog-search]', this.shell);
        if (searchInput) searchInput.value = url.searchParams.get('q') || '';
        this.updateNavTabs();
        this.updateCareerTools();
        window.history.replaceState(
            this.buildHistoryState(url.searchParams.get('post') ? 'detail' : 'list'),
            '',
            `${url.pathname}${url.search}`,
        );
        this.loadDiscovery();
        const postId = Number(url.searchParams.get('post') || 0);
        if (postId) {
            this.showDetail(postId, { historyMode: 'none', scrollToTop: false });
            return;
        }
        this.updateSortTabs();
        this.showCurrentListView();
        this.refreshCurrentList();
    }

    bindEvents() {
        this.shell.addEventListener('click', (event) => this.handleClick(event));
        this.shell.addEventListener('change', (event) => this.handleChange(event));
        this.shell.addEventListener('input', (event) => this.handleInput(event));
        this.shell.addEventListener('keydown', (event) => this.handleKeydown(event));
        window.addEventListener('popstate', (event) => this.handlePopState(event));
        window.addEventListener('scroll', () => {
            this.handleReadingScroll();
            if (this.tunerScrollFrame) return;
            this.tunerScrollFrame = window.requestAnimationFrame(() => {
                this.tunerScrollFrame = null;
                this.updateTunerCompactState();
            });
        }, { passive: true });
        window.addEventListener('pagehide', () => this.stopReadingSession({ useBeacon: true }));
        document.addEventListener('visibilitychange', () => this.handleReadingVisibility());
        $('[data-blog-report-form]', this.shell)?.addEventListener('submit', (event) => this.submitReport(event));
        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') {
                this.closeComposer();
                this.closeCommentPanels();
            }
        });
        document.addEventListener('click', (event) => {
            const toolbar = event.target.closest('[data-blog-comment-toolbar]');
            const customPanel = event.target.closest('[data-blog-comment-custom-emoji-panel]');
            const emojiPicker = event.target.closest('.emoji-picker');
            const userPopover = event.target.closest('[data-blog-user-popover]');
            const userTrigger = event.target.closest('[data-blog-user-menu]');
            if (!toolbar && !customPanel && !emojiPicker) {
                this.closeCommentPanels();
            }
            if (!userPopover && !userTrigger) {
                this.closeUserPopover();
            }
        });
    }

    handleClick(event) {
        const modal = $('[data-blog-composer-modal]', this.shell);
        if (modal && !modal.hidden && event.target === modal) {
            this.closeComposer();
            return;
        }

        const actionButton = event.target.closest('[data-blog-action]');
        if (actionButton) {
            this.handleAction(actionButton.dataset.blogAction);
            return;
        }

        const sortButton = event.target.closest('[data-blog-sort]');
        if (sortButton) {
            this.state.currentSort = sortButton.dataset.blogSort || 'latest';
            this.state.page = 1;
            this.state.currentNav = 'feed';
            this.updateNavTabs();
            this.showView('feed');
            this.updateListUrl();
            this.loadFeed();
            return;
        }

        const navButton = event.target.closest('[data-blog-nav]');
        if (navButton) {
            this.setNav(navButton.dataset.blogNav || 'feed');
            return;
        }

        const sectionButton = event.target.closest('[data-blog-section]');
        if (sectionButton) {
            this.setSection(sectionButton.dataset.blogSection || '');
            return;
        }

        const myFilterButton = event.target.closest('[data-blog-myposts-filter]');
        if (myFilterButton) {
            this.setMyPostsFilter(myFilterButton.dataset.blogMypostsFilter || 'all');
            return;
        }

        const loadMore = event.target.closest('[data-blog-load-more]');
        if (loadMore) {
            this.state.page += 1;
            this.loadFeed({ append: true });
            return;
        }

        const clearAuthorFilterButton = event.target.closest('[data-blog-clear-author-filter]');
        if (clearAuthorFilterButton) {
            this.clearAuthorFilter();
            return;
        }

        const clearTagFilterButton = event.target.closest('[data-blog-clear-tag-filter]');
        if (clearTagFilterButton) {
            this.clearTagFilter();
            return;
        }

        const userMenuButton = event.target.closest('[data-blog-user-menu]');
        if (userMenuButton) {
            this.openUserPopover(userMenuButton, {
                identity: userMenuButton.dataset.userIdentity || '',
                name: userMenuButton.dataset.userName || '',
                role: userMenuButton.dataset.userRole || '',
            });
            return;
        }

        const authorPostsButton = event.target.closest('[data-blog-author-posts]');
        if (authorPostsButton) {
            this.filterByAuthor(authorPostsButton.dataset.blogAuthorPosts || '', authorPostsButton.dataset.authorName || '');
            return;
        }

        const privateMessageButton = event.target.closest('[data-blog-private-message]');
        if (privateMessageButton) {
            this.openPrivateMessage(privateMessageButton.dataset.blogPrivateMessage || '');
            return;
        }

        const loadMoreMine = event.target.closest('[data-blog-my-load-more]');
        if (loadMoreMine) {
            this.state.myPage += 1;
            this.loadMyPosts({ append: true });
            return;
        }

        const loadMoreBookmarks = event.target.closest('[data-blog-bookmarks-load-more]');
        if (loadMoreBookmarks) {
            this.state.bmPage += 1;
            this.loadBookmarks({ append: true });
            return;
        }

        const toolbarButton = event.target.closest('[data-toolbar]');
        if (toolbarButton) {
            this.insertMarkdown(toolbarButton.dataset.toolbar || '');
            return;
        }

        const followButton = event.target.closest('[data-blog-follow-type]');
        if (followButton) {
            this.toggleFollow(
                followButton.dataset.blogFollowType || '',
                followButton.dataset.blogFollowKey || '',
            );
            return;
        }

        const composeModeButton = event.target.closest('[data-blog-compose-mode]');
        if (composeModeButton) {
            this.setComposerMode(composeModeButton.dataset.blogComposeMode || 'edit');
            return;
        }

        const likePostButton = event.target.closest('[data-like-post]');
        if (likePostButton) {
            this.toggleLike('post', Number(likePostButton.dataset.likePost));
            return;
        }

        const likeCommentButton = event.target.closest('[data-like-comment]');
        if (likeCommentButton) {
            this.toggleLike('comment', Number(likeCommentButton.dataset.likeComment));
            return;
        }

        const bookmarkButton = event.target.closest('[data-bookmark-post]');
        if (bookmarkButton) {
            this.toggleBookmark(Number(bookmarkButton.dataset.bookmarkPost));
            return;
        }

        const deletePostButton = event.target.closest('[data-delete-post]');
        if (deletePostButton) {
            this.deletePost(Number(deletePostButton.dataset.deletePost));
            return;
        }

        const editPostButton = event.target.closest('[data-edit-post]');
        if (editPostButton) {
            this.editPost(Number(editPostButton.dataset.editPost));
            return;
        }

        const pinPostButton = event.target.closest('[data-pin-post]');
        if (pinPostButton) {
            this.togglePin(Number(pinPostButton.dataset.pinPost));
            return;
        }

        const featurePostButton = event.target.closest('[data-feature-post]');
        if (featurePostButton) {
            this.toggleFeature(Number(featurePostButton.dataset.featurePost));
            return;
        }

        const hidePostButton = event.target.closest('[data-hide-post]');
        if (hidePostButton) {
            this.toggleVisibility(Number(hidePostButton.dataset.hidePost));
            return;
        }

        const toggleCommentsButton = event.target.closest('[data-toggle-comments]');
        if (toggleCommentsButton) {
            this.toggleComments(Number(toggleCommentsButton.dataset.toggleComments));
            return;
        }

        const deleteCommentButton = event.target.closest('[data-delete-comment]');
        if (deleteCommentButton) {
            this.deleteComment(Number(deleteCommentButton.dataset.deleteComment));
            return;
        }

        const replyButton = event.target.closest('[data-reply-to]');
        if (replyButton) {
            this.startReply(Number(replyButton.dataset.replyTo), replyButton.dataset.replyName || '');
            return;
        }

        const submitCommentButton = event.target.closest('[data-blog-submit-comment]');
        if (submitCommentButton) {
            this.submitComment();
            return;
        }

        const cancelReplyButton = event.target.closest('[data-blog-reply-cancel]');
        if (cancelReplyButton) {
            this.resetCommentDraft({ keepPicker: true });
            this.renderCommentDraftState();
            return;
        }

        const removeComposeImageButton = event.target.closest('[data-remove-image]');
        if (removeComposeImageButton) {
            this.removeComposeImage(Number(removeComposeImageButton.dataset.removeImage));
            return;
        }

        const removeCommentAttachmentButton = event.target.closest('[data-remove-comment-attachment]');
        if (removeCommentAttachmentButton) {
            this.removeCommentAttachment(Number(removeCommentAttachmentButton.dataset.removeCommentAttachment));
            return;
        }

        const removeCommentEmojiButton = event.target.closest('[data-remove-comment-emoji]');
        if (removeCommentEmojiButton) {
            this.removeCommentEmoji(Number(removeCommentEmojiButton.dataset.removeCommentEmoji));
            return;
        }

        const removeSelectedUserButton = event.target.closest('[data-remove-user]');
        if (removeSelectedUserButton) {
            this.state.selectedUsers.splice(Number(removeSelectedUserButton.dataset.removeUser), 1);
            this.renderSelectedUsers();
            return;
        }

        const pickUserButton = event.target.closest('[data-pick-user]');
        if (pickUserButton) {
            this.pickUser(pickUserButton.dataset.pickUser || '');
            return;
        }

        const commentEmojiToggle = event.target.closest('[data-blog-comment-emoji-toggle]');
        if (commentEmojiToggle) {
            this.toggleCommentEmojiPicker();
            return;
        }

        const commentFormatButton = event.target.closest('[data-blog-comment-format]');
        if (commentFormatButton) {
            this.insertCommentSnippet(commentFormatButton.dataset.blogCommentFormat || '');
            return;
        }

        const customEmojiToggle = event.target.closest('[data-blog-comment-custom-emoji-toggle]');
        if (customEmojiToggle) {
            this.toggleCustomEmojiPanel();
            return;
        }

        const closeCustomEmojiPanelButton = event.target.closest('[data-blog-close-custom-emoji-panel]');
        if (closeCustomEmojiPanelButton) {
            this.closeCommentPanels();
            return;
        }

        const commentUploadButton = event.target.closest('[data-blog-comment-upload]');
        if (commentUploadButton) {
            $('[data-blog-comment-file-input]', this.shell)?.click();
            return;
        }

        const customEmojiButton = event.target.closest('[data-blog-custom-emoji-pick]');
        if (customEmojiButton) {
            this.addCommentCustomEmoji(customEmojiButton.dataset.fileHash || '');
            return;
        }

        const tocLink = event.target.closest('[data-blog-toc-target]');
        if (tocLink) {
            event.preventDefault();
            document.getElementById(tocLink.dataset.blogTocTarget)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
            return;
        }

        const tagButton = event.target.closest('[data-blog-tag]');
        if (tagButton) {
            const tag = tagButton.dataset.blogTag || '';
            this.setTagFilter(tag);
            return;
        }

        const openPostButton = event.target.closest('[data-blog-open-post]');
        if (openPostButton) {
            event.preventDefault();
            this.showDetail(Number(openPostButton.dataset.blogOpenPost));
            return;
        }

        const internalPostLink = event.target.closest('.blog-detail__body a[href]');
        if (internalPostLink) {
            const targetUrl = new URL(internalPostLink.href, window.location.origin);
            const targetPostId = Number(targetUrl.searchParams.get('post') || 0);
            if (targetUrl.origin === window.location.origin && targetUrl.pathname === '/blog' && targetPostId) {
                event.preventDefault();
                this.showDetail(targetPostId);
                return;
            }
        }

        const opportunitySaveButton = event.target.closest('[data-blog-opportunity-save]');
        if (opportunitySaveButton) {
            const currentState = opportunitySaveButton.dataset.currentState || '';
            this.setOpportunityState(
                Number(opportunitySaveButton.dataset.blogOpportunitySave),
                currentState ? 'none' : 'saved',
            );
            return;
        }

        const reportPostButton = event.target.closest('[data-blog-report-post]');
        if (reportPostButton) {
            this.openReport('post', Number(reportPostButton.dataset.blogReportPost));
            return;
        }

        if (event.target.closest('[data-blog-report-cancel]')) {
            $('[data-blog-report-dialog]', this.shell)?.close();
            this.reportTarget = null;
            return;
        }

        const postCard = event.target.closest('[data-blog-post-id]');
        if (postCard && !event.target.closest('button, a, input, textarea, select')) {
            this.showDetail(Number(postCard.dataset.blogPostId));
        }
    }

    handleChange(event) {
        if (event.target.matches('[data-blog-career-region], [data-blog-career-type], [data-blog-career-deadline], [data-blog-career-state]')) {
            this.state.careerFilters = normalizeCareerFilters({
                region: $('[data-blog-career-region]', this.shell)?.value || '',
                opportunityType: $('[data-blog-career-type]', this.shell)?.value || '',
                deadlineDays: $('[data-blog-career-deadline]', this.shell)?.value || '',
                userState: $('[data-blog-career-state]', this.shell)?.value || '',
            });
            this.state.page = 1;
            this.updateListUrl();
            this.loadFeed();
            return;
        }
        if (event.target.matches('[data-blog-opportunity-state]')) {
            this.setOpportunityState(Number(event.target.dataset.blogOpportunityState), event.target.value);
            return;
        }
        if (event.target.matches('[data-blog-compose-file-input]')) {
            this.handleImageUpload(event.target.files, { context: 'compose' });
            return;
        }
        if (event.target.matches('[data-blog-comment-file-input]')) {
            this.handleImageUpload(event.target.files, { context: 'comment' });
            return;
        }
        if (event.target.matches('[data-blog-compose-visibility]')) {
            this.updateVisibilityOptions(event.target.value);
            this.scheduleComposerRecovery();
            return;
        }
        if (event.target.matches('[data-blog-compose-author-mode]')) {
            this.updateAuthorModeHint(event.target.value);
            this.scheduleComposerRecovery();
            return;
        }
        if (event.target.matches('[data-blog-compose-visibility], [data-blog-compose-section], [data-blog-compose-class], [data-blog-compose-comments]')) {
            this.scheduleComposerRecovery();
        }
    }

    handleInput(event) {
        if (event.target.matches('[data-blog-search]')) {
            window.clearTimeout(this.searchTimer);
            this.searchTimer = window.setTimeout(() => {
                this.state.page = 1;
                this.updateListUrl();
                this.loadFeed();
            }, 320);
            return;
        }

        if (event.target.matches('[data-blog-compose-title], [data-blog-compose-content], [data-blog-compose-tags]')) {
            this.updateComposerMetrics();
            this.scheduleComposerRecovery();
            return;
        }

        if (event.target.matches('[data-blog-compose-user-search]')) {
            const keyword = event.target.value.trim();
            window.clearTimeout(this.userSearchTimer);
            this.userSearchTimer = window.setTimeout(() => {
                this.searchUsers(keyword);
            }, 260);
            return;
        }

        if (event.target.matches('[data-blog-comment-input]')) {
            this.autoSizeCommentInput(event.target);
        }
    }

    handleAction(action) {
        switch (action) {
        case 'compose':
            this.openComposer();
            break;
        case 'close-composer':
            this.closeComposer();
            break;
        case 'back-to-feed':
            this.backToList();
            break;
        case 'back-detail-context':
            this.backFromDetail();
            break;
        case 'upload-image':
            $('[data-blog-compose-file-input]', this.shell)?.click();
            break;
        case 'save-draft':
            this.savePost('draft');
            break;
        case 'publish':
            this.savePost('published');
            break;
        default:
            break;
        }
    }

    updateSortTabs() {
        $$('[data-blog-sort]', this.shell).forEach((button) => {
            button.classList.toggle(
                'is-active',
                this.state.currentNav === 'feed' && button.dataset.blogSort === this.state.currentSort,
            );
        });
    }

    updateNavTabs() {
        $$('[data-blog-nav]', this.shell).forEach((button) => {
            button.classList.toggle('is-active', button.dataset.blogNav === this.state.currentNav);
        });
        this.updateSortTabs();
    }

    setNav(nav) {
        this.state.currentNav = nav;
        this.updateNavTabs();
        this.updateListUrl();
        if (nav === 'feed') {
            this.showView('feed');
            this.state.page = 1;
            this.loadFeed();
            return;
        }
        if (nav === 'following') {
            this.showView('feed');
            this.state.page = 1;
            this.loadFeed();
            return;
        }
        if (nav === 'my-posts') {
            this.showView('my-posts');
            this.state.myPage = 1;
            this.loadMyPosts();
            return;
        }
        if (nav === 'bookmarks') {
            this.showView('bookmarks');
            this.state.bmPage = 1;
            this.loadBookmarks();
        }
    }

    updateTunerCompactState() {
        const consoleNode = $('[data-blog-header]', this.shell);
        if (!consoleNode) return;
        const isDetail = this.state.currentView === 'detail';
        if (this.tunerExpandedTop === null && !isDetail) {
            this.tunerExpandedTop = consoleNode.getBoundingClientRect().top + window.scrollY;
        }
        const collapseAt = Math.max(96, Number(this.tunerExpandedTop || 0) + 128);
        const expandAt = Math.max(48, Number(this.tunerExpandedTop || 0) + 36);
        const wasCompact = consoleNode.classList.contains('is-compact');
        // Separate thresholds prevent the tuner's own height change from
        // bouncing the browser back and forth across one cutoff point.
        const compact = isDetail || (wasCompact ? window.scrollY > expandAt : window.scrollY > collapseAt);
        consoleNode.classList.toggle('is-compact', compact);
        consoleNode.classList.toggle('is-detail-compact', isDetail);
        this.shell.classList.toggle('is-tuner-compact', compact);
    }

    animateFeedReveal(container) {
        if (!container) return;
        window.clearTimeout(this.channelRevealTimer);
        container.classList.remove('is-channel-reveal');
        $$(':scope > .blog-post-card', container).forEach((card, index) => {
            card.style.setProperty('--channel-card-index', String(Math.min(index, 7)));
        });
        window.requestAnimationFrame(() => container.classList.add('is-channel-reveal'));
        this.channelRevealTimer = window.setTimeout(() => container.classList.remove('is-channel-reveal'), 1100);
    }

    setSection(sectionKey) {
        const normalizedKey = String(sectionKey || '').trim().toLowerCase();
        if (normalizedKey && this.state.sections.length && !this.state.sections.some((item) => item.section_key === normalizedKey)) {
            showToast('这个板块暂不可用', 'warning');
            return;
        }
        this.state.currentSection = normalizedKey;
        this.state.currentNav = 'feed';
        this.state.page = 1;
        this.state.authorFilter = null;
        this.state.tagFilter = null;
        const searchInput = $('[data-blog-search]', this.shell);
        if (searchInput) searchInput.value = '';
        this.updateNavTabs();
        this.updateAuthorFilterBanner();
        this.updateTagFilterBanner();
        this.showView('feed');
        this.renderSectionTabs();
        this.renderSectionIntro();
        this.updateListUrl();
        this.loadFeed();
        this.loadDiscovery();
    }

    updateListUrl() {
        const url = new URL('/blog', window.location.origin);
        if (this.state.currentNav === 'feed' && this.state.currentSection) {
            url.searchParams.set('section', this.state.currentSection);
        }
        if (this.state.currentNav !== 'feed') url.searchParams.set('view', this.state.currentNav);
        if (this.state.currentSort !== 'latest') url.searchParams.set('sort', this.state.currentSort);
        const query = $('[data-blog-search]', this.shell)?.value?.trim() || '';
        if (query) url.searchParams.set('q', query);
        if (this.state.currentNav === 'feed' && this.state.currentSection === 'career') {
            const filters = normalizeCareerFilters(this.state.careerFilters);
            if (filters.region) url.searchParams.set('region', filters.region);
            if (filters.opportunityType) url.searchParams.set('type', filters.opportunityType);
            if (filters.deadlineDays) url.searchParams.set('deadline', filters.deadlineDays);
            if (filters.userState) url.searchParams.set('state', filters.userState);
        }
        const snapshot = this.buildListSnapshot();
        this.lastListSnapshot = snapshot;
        window.history.replaceState(
            { blog: { view: 'list', postId: null, snapshot } },
            '',
            `${url.pathname}${url.search}`,
        );
    }

    backToList() {
        const snapshot = this.lastListSnapshot || this.buildListSnapshot();
        this.detailHasListHistory = false;
        this.detailPreviousIsPost = false;
        this.state.detailPostId = null;
        this.state.detailPost = null;
        this.restoreListSnapshot(snapshot);
        this.showCurrentListView();
        this.refreshCurrentList();
        this.loadDiscovery();
        this.updateListUrl();
        window.requestAnimationFrame(() => window.scrollTo({ top: Number(snapshot.scrollY || 0), behavior: 'auto' }));
    }

    backFromDetail() {
        if (this.detailHasListHistory) {
            window.history.back();
            return;
        }
        this.backToList();
    }

    sectionByKey(sectionKey) {
        const normalizedKey = String(sectionKey || '').trim().toLowerCase();
        const configured = this.state.sections.find((item) => item.section_key === normalizedKey);
        if (configured) return configured;
        const defaults = {
            general: { section_key: 'general', name: '杂谈与故事', short_name: '杂谈', description: '小说、随笔、校园故事、阅读札记与成长片段。', icon: '✦', accent_color: '#2563eb' },
            technology: { section_key: 'technology', name: '科技前沿', short_name: '科技', icon: '⌁', accent_color: '#0f766e' },
            humanities: { section_key: 'humanities', name: '人文视界', short_name: '人文', icon: '文', accent_color: '#b45309' },
            computer: { section_key: 'computer', name: '计算机', short_name: '计算机', icon: '</>', accent_color: '#4f46e5' },
            ai: { section_key: 'ai', name: 'AI 新知', short_name: 'AI', icon: 'AI', accent_color: '#7c3aed' },
            career: { section_key: 'career', name: '毕业新征程', short_name: '就业', icon: '→', accent_color: '#e11d48' },
        };
        return defaults[normalizedKey] || null;
    }

    sectionAccent(section) {
        const value = String(section?.accent_color || '').trim();
        return /^#[0-9a-f]{6}$/i.test(value) ? value : '#2563eb';
    }

    renderSectionTabs() {
        const container = $('[data-blog-section-tabs]', this.shell);
        if (!container || !this.state.sections.length) return;
        const total = this.state.sections.reduce((sum, item) => sum + Number(item.post_count || 0), 0);
        const allActive = !this.state.currentSection;
        const allSignalClass = total ? ' has-content' : ' is-empty';
        const tabs = [
            `
                <button class="blog-section-tab${allActive ? ' is-active' : ''}${allSignalClass}" data-blog-section="" data-blog-section-count="${total}" type="button" role="tab" aria-selected="${allActive ? 'true' : 'false'}" aria-controls="blog-feed-panel" tabindex="${allActive ? '0' : '-1'}" style="--section-accent:#2563eb">
                    <span class="blog-section-tab__icon" aria-hidden="true">◎</span>
                    <span class="blog-section-tab__name">全部</span>
                    <span class="blog-section-tab__count">${formatCompactNumber(total)}</span>
                </button>
            `,
            ...this.state.sections.map((section) => {
                const active = section.section_key === this.state.currentSection;
                const postCount = Number(section.post_count || 0);
                return `
                    <button class="blog-section-tab${active ? ' is-active' : ''}${postCount ? ' has-content' : ' is-empty'}" data-blog-section="${escapeHtml(section.section_key || '')}" data-blog-section-count="${postCount}" type="button" role="tab" aria-selected="${active ? 'true' : 'false'}" aria-controls="blog-feed-panel" tabindex="${active ? '0' : '-1'}" style="--section-accent:${this.sectionAccent(section)}">
                        <span class="blog-section-tab__icon" aria-hidden="true">${escapeHtml(section.icon || '•')}</span>
                        <span class="blog-section-tab__name">${escapeHtml(section.short_name || section.name || '板块')}</span>
                        <span class="blog-section-tab__count">${formatCompactNumber(postCount)}</span>
                    </button>
                `;
            }),
        ];
        container.innerHTML = tabs.join('');
    }

    renderSectionIntro() {
        const intro = $('[data-blog-section-intro]', this.shell);
        if (!intro) return;
        const section = this.sectionByKey(this.state.currentSection);
        if (!section) {
            intro.style.removeProperty('--section-accent');
            intro.innerHTML = '<span>全频段</span><p>从不同方向遇见新问题、新知识和下一段旅程。</p>';
            return;
        }
        intro.style.setProperty('--section-accent', this.sectionAccent(section));
        const following = this.followKeys.has(`section:${section.section_key}`);
        intro.innerHTML = `
            <span>${escapeHtml(section.name || '')}</span>
            <p>${escapeHtml(section.description || '')}</p>
            <button type="button" class="blog-section-follow${following ? ' is-following' : ''}" data-blog-follow-type="section" data-blog-follow-key="${escapeHtml(section.section_key)}">${following ? '已关注' : '关注板块'}</button>
        `;
    }

    updateCareerTools() {
        const tools = $('[data-blog-career-filters]', this.shell);
        const isCareer = this.state.currentSection === 'career';
        if (tools) tools.hidden = !isCareer;
        const latestSort = $('[data-blog-sort="latest"]', this.shell);
        if (latestSort) latestSort.textContent = isCareer ? '优先推荐' : '最新';
        const searchInput = $('[data-blog-search]', this.shell);
        if (searchInput) {
            searchInput.placeholder = isCareer
                ? '搜单位、岗位、专业或文章...'
                : '搜标题、正文或标签...';
        }
        const filters = normalizeCareerFilters(this.state.careerFilters);
        this.state.careerFilters = filters;
        const fieldValues = {
            '[data-blog-career-region]': filters.region,
            '[data-blog-career-type]': filters.opportunityType,
            '[data-blog-career-deadline]': filters.deadlineDays,
            '[data-blog-career-state]': filters.userState,
        };
        Object.entries(fieldValues).forEach(([selector, value]) => {
            const field = $(selector, this.shell);
            if (field) field.value = value;
        });
    }

    async loadFollows() {
        try {
            const data = await api.get('/api/blog/follows');
            this.followKeys = new Set((data.follows || []).map((item) => `${item.target_type}:${item.target_key}`));
            this.renderSectionIntro();
        } catch (error) {
            this.followKeys = new Set();
        }
    }

    async toggleFollow(targetType, targetKey) {
        const key = `${targetType}:${targetKey}`;
        const following = !this.followKeys.has(key);
        try {
            const data = await api.post('/api/blog/follows', {
                target_type: targetType,
                target_key: targetKey,
                following,
            });
            if (data.following) this.followKeys.add(key);
            else this.followKeys.delete(key);
            $$(`[data-blog-follow-type="${targetType}"]`, this.shell)
                .filter((button) => button.dataset.blogFollowKey === targetKey)
                .forEach((button) => {
                button.classList.toggle('is-following', Boolean(data.following));
                button.textContent = data.following ? (targetType === 'section' ? '已关注' : '已关注作者') : (targetType === 'section' ? '关注板块' : '关注作者');
            });
            showToast(data.following ? '关注成功，新内容会进入“我的关注”' : '已取消关注', 'success');
            if (this.state.currentNav === 'following' && !data.following) this.loadFeed();
        } catch (error) {
            showToast(error.message || '关注操作失败', 'error');
        }
    }

    sectionBadgeHtml(sectionKey, className = '') {
        const section = this.sectionByKey(sectionKey);
        if (!section) return '';
        return `<span class="blog-section-badge ${escapeHtml(className)}" style="--section-accent:${this.sectionAccent(section)}"><span>${escapeHtml(section.icon || '•')}</span>${escapeHtml(section.short_name || section.name || '')}</span>`;
    }

    postCoverFallbackHtml(post = {}, variant = 'card') {
        const section = this.sectionByKey(post.section_key) || {};
        const automatedNews = post.author?.role === 'assistant';
        const label = automatedNews ? '栏目封面 · 非新闻原图' : '精选内容封面';
        return `
            <div class="blog-cover-art blog-cover-art--${escapeHtml(variant)}" style="--cover-accent:${this.sectionAccent(section)}" role="img" aria-label="${escapeHtml(label)}">
                <span class="blog-cover-art__grid" aria-hidden="true"></span>
                <span class="blog-cover-art__eyebrow">${escapeHtml(label)}</span>
                <span class="blog-cover-art__icon" aria-hidden="true">${escapeHtml(section.icon || '✦')}</span>
                <span class="blog-cover-art__channel">${escapeHtml(section.short_name || section.name || '博客')}</span>
                <span class="blog-cover-art__signal" aria-hidden="true">LS / ${escapeHtml(String(post.id || '00').padStart(2, '0'))}</span>
            </div>
        `;
    }

    postCoverMediaHtml(post = {}, variant = 'card', { genericFallback = false } = {}) {
        if (post.cover_image_hash) {
            return `<img class="blog-cover-image" src="/api/blog/image/${escapeHtml(post.cover_image_hash)}" alt="" loading="lazy" decoding="async">`;
        }
        if (post.cover_image_kind === 'editorial' || post.author?.role === 'assistant' || genericFallback) {
            return this.postCoverFallbackHtml(post, variant);
        }
        return '';
    }

    opportunityDeadlineLabel(opportunity = {}) {
        const days = Number(opportunity.deadline_days);
        if (!Number.isFinite(days)) return '截止时间以官方公告为准';
        if (days < 0) return '已截止';
        if (days === 0) return '今天截止';
        if (days <= 3) return `${days} 天后截止`;
        const date = new Date(opportunity.deadline_at || '');
        if (Number.isNaN(date.getTime())) return `${days} 天后截止`;
        return `${date.getMonth() + 1} 月 ${date.getDate()} 日截止`;
    }

    opportunityCardHtml(opportunity = {}) {
        if (!opportunity?.id) return '';
        const regions = [...(opportunity.regions || []), opportunity.city].filter(Boolean).slice(0, 3);
        const targets = (opportunity.target_groups || []).slice(0, 2);
        const state = opportunity.user_state || '';
        const deadlineDays = Number(opportunity.deadline_days);
        const deadlineClass = Number.isFinite(deadlineDays) && deadlineDays <= 3 ? ' is-urgent' : '';
        const officialLink = opportunity.application_url || opportunity.source_url || '';
        return `
            <section class="blog-opportunity-card" aria-label="就业机会摘要">
                <div class="blog-opportunity-card__topline">
                    <span class="blog-opportunity-source is-level-${escapeHtml(String(opportunity.source_level || 'C').toLowerCase())}">${escapeHtml(opportunity.source_level_label || '来源待核验')}</span>
                    <span class="blog-opportunity-deadline${deadlineClass}">${escapeHtml(this.opportunityDeadlineLabel(opportunity))}</span>
                </div>
                <strong class="blog-opportunity-card__employer">${escapeHtml(opportunity.employer_name || '招聘单位以原公告为准')}</strong>
                <div class="blog-opportunity-card__facts">
                    <span>${escapeHtml(OPPORTUNITY_TYPE_LABELS[opportunity.opportunity_type] || '就业机会')}</span>
                    ${regions.map((item) => `<span>${escapeHtml(item)}</span>`).join('')}
                    ${targets.map((item) => `<span>${escapeHtml(item)}</span>`).join('')}
                </div>
                <div class="blog-opportunity-card__actions">
                    <button type="button" data-blog-opportunity-save="${opportunity.id}" data-current-state="${escapeHtml(state)}">${escapeHtml(state ? OPPORTUNITY_STATE_LABELS[state] || '已跟进' : '收藏机会')}</button>
                    ${officialLink ? `<a href="${escapeHtml(officialLink)}" target="_blank" rel="noopener noreferrer">查看原公告</a>` : '<span>原公告入口待核验</span>'}
                </div>
            </section>
        `;
    }

    opportunityDetailHtml(opportunity = {}) {
        if (!opportunity?.id) return '';
        const facts = [
            ['单位/项目', opportunity.employer_name],
            ['岗位/机会', opportunity.positions_text],
            ['地区', [...(opportunity.regions || []), opportunity.city].filter(Boolean).join('、')],
            ['适合对象', (opportunity.target_groups || []).join('、')],
            ['学历要求', opportunity.education_text],
            ['专业要求', (opportunity.majors || []).join('、')],
            ['招聘人数', opportunity.headcount_text],
            ['薪酬说明', opportunity.compensation_text],
            ['报名方式', opportunity.application_method],
        ].filter((item) => item[1]);
        const officialLink = opportunity.application_url || opportunity.source_url || '';
        return `
            <section class="blog-opportunity-detail" aria-label="就业机会关键信息">
                <div class="blog-opportunity-detail__header">
                    <div>
                        <span class="blog-opportunity-source is-level-${escapeHtml(String(opportunity.source_level || 'C').toLowerCase())}">${escapeHtml(opportunity.source_level_label || '来源待核验')}</span>
                        <h2>${escapeHtml(opportunity.employer_name || '就业机会')}</h2>
                        <p>${escapeHtml(this.opportunityDeadlineLabel(opportunity))} · 最近核验 ${escapeHtml(timeAgo(opportunity.last_verified_at) || '时间未知')}</p>
                    </div>
                    ${officialLink ? `<a class="btn btn-primary btn-sm" href="${escapeHtml(officialLink)}" target="_blank" rel="noopener noreferrer">打开官方公告</a>` : ''}
                </div>
                ${facts.length ? `<dl class="blog-opportunity-detail__grid">${facts.map(([label, value]) => `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`).join('')}</dl>` : ''}
                <div class="blog-opportunity-detail__workflow">
                    <label for="blog-opportunity-state-${opportunity.id}">我的求职进度</label>
                    <select id="blog-opportunity-state-${opportunity.id}" data-blog-opportunity-state="${opportunity.id}">
                        <option value=""${!opportunity.user_state ? ' selected' : ''}>尚未跟进</option>
                        ${Object.entries(OPPORTUNITY_STATE_LABELS).map(([value, label]) => `<option value="${value}"${opportunity.user_state === value ? ' selected' : ''}>${escapeHtml(label)}</option>`).join('')}
                    </select>
                </div>
                <p class="blog-opportunity-detail__safety">请以官方域名和原公告为准。任何收费内推、押金、培训贷或提前索要身份证原件、银行卡密码的行为都应警惕。</p>
            </section>
        `;
    }

    updateDetailTopbar(post = this.state.detailPost) {
        const section = this.sectionByKey(post?.section_key);
        const sectionNode = $('[data-blog-detail-section]', this.shell);
        const titleNode = $('[data-blog-detail-title]', this.shell);
        if (sectionNode) sectionNode.textContent = section?.name || '博客中心';
        if (titleNode) titleNode.textContent = post?.title || '文章详情';
        const backContext = $('[data-blog-detail-back-context]', this.shell);
        const backLabel = $('[data-blog-detail-back-label]', this.shell);
        if (backLabel) backLabel.textContent = this.detailPreviousIsPost ? '返回上一篇文章' : '返回博客列表';
        if (backContext) {
            backContext.textContent = this.detailPreviousIsPost
                ? '回到刚才读到的文章'
                : `返回${section?.name || '博客列表'}并恢复浏览位置`;
        }
    }

    setMyPostsFilter(filterValue) {
        this.state.myPostsFilter = filterValue === 'all' ? null : filterValue;
        $$('[data-blog-myposts-filter]', this.shell).forEach((button) => {
            button.classList.toggle('is-active', button.dataset.blogMypostsFilter === filterValue);
        });
        this.state.myPage = 1;
        this.loadMyPosts();
    }

    showView(viewName) {
        if (this.state.currentView === 'detail' && viewName !== 'detail') {
            this.stopReadingSession();
            this.teardownDetailToc();
        }
        $$('[data-blog-view]', this.shell).forEach((view) => {
            view.hidden = view.dataset.blogView !== viewName;
        });
        this.state.currentView = viewName;
        const isDetail = viewName === 'detail';
        this.shell.classList.toggle('is-detail-mode', isDetail);
        const detailTopbar = $('[data-blog-detail-topbar]', this.shell);
        if (detailTopbar) detailTopbar.hidden = !isDetail;
        this.updateTunerCompactState();
    }

    showCurrentListView() {
        if (this.state.currentNav === 'my-posts') {
            this.showView('my-posts');
            return;
        }
        if (this.state.currentNav === 'bookmarks') {
            this.showView('bookmarks');
            return;
        }
        this.showView('feed');
    }

    updateAuthorFilterBanner() {
        const banner = $('[data-blog-author-filter]', this.shell);
        const label = $('[data-blog-author-filter-label]', this.shell);
        if (!banner || !label) return;
        const filter = this.state.authorFilter;
        banner.hidden = !filter?.identity;
        label.textContent = filter?.identity ? `正在查看 ${filter.name || '该用户'} 的帖子` : '';
    }

    updateTagFilterBanner() {
        const banner = $('[data-blog-tag-filter]', this.shell);
        const label = $('[data-blog-tag-filter-label]', this.shell);
        if (!banner || !label) return;
        const tag = this.state.tagFilter;
        banner.hidden = !tag;
        label.textContent = tag ? `正在浏览 #${tag}` : '';
        $$('[data-blog-tag]', this.shell).forEach((button) => {
            button.classList.toggle('is-active', button.dataset.blogTag === tag);
        });
    }

    filterByAuthor(identity, name = '') {
        const normalizedIdentity = String(identity || '').trim();
        if (!normalizedIdentity) return;
        this.state.authorFilter = {
            identity: normalizedIdentity,
            name: String(name || '').trim() || '该用户',
        };
        this.state.tagFilter = null;
        this.state.currentNav = 'feed';
        this.state.currentView = 'feed';
        this.state.page = 1;
        const searchInput = $('[data-blog-search]', this.shell);
        if (searchInput) searchInput.value = '';
        this.updateNavTabs();
        this.showView('feed');
        this.updateAuthorFilterBanner();
        this.updateTagFilterBanner();
        this.closeUserPopover();
        this.loadFeed();
    }

    clearAuthorFilter() {
        if (!this.state.authorFilter) return;
        this.state.authorFilter = null;
        this.state.page = 1;
        this.updateAuthorFilterBanner();
        this.loadFeed();
    }

    setTagFilter(tag) {
        const normalizedTag = String(tag || '').trim();
        if (!normalizedTag) return;
        this.state.tagFilter = normalizedTag;
        this.state.authorFilter = null;
        this.state.currentNav = 'feed';
        this.state.currentView = 'feed';
        this.state.page = 1;
        const searchInput = $('[data-blog-search]', this.shell);
        if (searchInput) searchInput.value = '';
        this.updateNavTabs();
        this.showView('feed');
        this.updateAuthorFilterBanner();
        this.updateTagFilterBanner();
        this.loadFeed();
    }

    clearTagFilter() {
        if (!this.state.tagFilter) return;
        this.state.tagFilter = null;
        this.state.page = 1;
        this.updateTagFilterBanner();
        this.loadFeed();
    }

    openPrivateMessage(identity) {
        const normalizedIdentity = String(identity || '').trim();
        if (!normalizedIdentity) return;
        if (normalizedIdentity === this.userIdentity) {
            showToast('不能给自己发送私信', 'warning');
            return;
        }
        const url = new URL('/profile', window.location.origin);
        url.searchParams.set('section', 'private');
        url.searchParams.set('tab', 'private_message');
        url.searchParams.set('contact', normalizedIdentity);
        window.location.href = `${url.pathname}${url.search}`;
    }

    closeUserPopover() {
        const popover = $('[data-blog-user-popover]', this.shell);
        if (!popover) return;
        popover.hidden = true;
        popover.innerHTML = '';
    }

    openUserPopover(trigger, user) {
        const identity = String(user?.identity || '').trim();
        const name = String(user?.name || '').trim() || '该用户';
        const role = String(user?.role || '').trim().toLowerCase();
        if (!identity || role === 'assistant') return;

        const popover = $('[data-blog-user-popover]', this.shell);
        if (!popover) return;
        const isSelf = identity === this.userIdentity;
        const isFollowing = this.followKeys.has(`author:${identity}`);
        popover.innerHTML = `
            <div class="blog-user-popover__name">${escapeHtml(name)}</div>
            <div class="blog-user-popover__actions">
                <button type="button" class="blog-user-popover__btn" data-blog-author-posts="${escapeHtml(identity)}" data-author-name="${escapeHtml(name)}">ta的帖子</button>
                ${isSelf ? '' : `<button type="button" class="blog-user-popover__btn${isFollowing ? ' is-following' : ''}" data-blog-follow-type="author" data-blog-follow-key="${escapeHtml(identity)}">${isFollowing ? '已关注作者' : '关注作者'}</button>`}
                <button type="button" class="blog-user-popover__btn blog-user-popover__btn--primary" data-blog-private-message="${escapeHtml(identity)}" ${isSelf ? 'disabled title="不能给自己发送私信"' : ''}>私信</button>
            </div>
        `;
        popover.hidden = false;

        const rect = trigger.getBoundingClientRect();
        const popoverRect = popover.getBoundingClientRect();
        const gap = 8;
        const nextLeft = Math.min(
            Math.max(rect.left, gap),
            Math.max(window.innerWidth - popoverRect.width - gap, gap),
        );
        const nextTop = Math.min(
            rect.bottom + gap,
            Math.max(window.innerHeight - popoverRect.height - gap, gap),
        );
        popover.style.left = `${nextLeft}px`;
        popover.style.top = `${nextTop}px`;
    }

    async loadDiscovery() {
        const controller = this.beginRequest('discovery');
        this.renderDiscoveryLoading();
        try {
            const url = new URL('/api/blog/discovery', window.location.origin);
            if (this.state.currentSection) url.searchParams.set('section', this.state.currentSection);
            const data = await api.get(`${url.pathname}${url.search}`, { signal: controller.signal });
            this.state.discovery = data;
            this.renderDiscovery(data);
        } catch (error) {
            if (error?.name === 'AbortError') return;
            this.renderDiscoveryError(error.message || '探索内容加载失败');
        }
    }

    renderDiscoveryLoading() {
        const spotlight = $('[data-blog-spotlight]', this.shell);
        if (spotlight && !spotlight.innerHTML.trim()) {
            spotlight.innerHTML = this.skeletonHtml(1);
        }
        const trending = $('[data-blog-trending-list]', this.shell);
        if (trending && !trending.innerHTML.trim()) {
            trending.innerHTML = this.railEmptyHtml('正在整理热议内容...');
        }
        const tags = $('[data-blog-hot-tags]', this.shell);
        if (tags && !tags.innerHTML.trim()) {
            tags.innerHTML = this.railEmptyHtml('正在读取热门标签...');
        }
        const authors = $('[data-blog-active-authors]', this.shell);
        if (authors && !authors.innerHTML.trim()) {
            authors.innerHTML = this.railEmptyHtml('正在计算活跃作者...');
        }
    }

    renderDiscoveryError(message) {
        const spotlight = $('[data-blog-spotlight]', this.shell);
        if (spotlight) spotlight.innerHTML = `<div class="blog-discovery-empty">${escapeHtml(message)}</div>`;
        $('[data-blog-trending-list]', this.shell)?.replaceChildren();
        $('[data-blog-hot-tags]', this.shell)?.replaceChildren();
        $('[data-blog-active-authors]', this.shell)?.replaceChildren();
    }

    renderDiscovery(data = {}) {
        if (Array.isArray(data.sections) && data.sections.length) {
            this.state.sections = data.sections;
            if (this.state.currentSection && !this.state.sections.some((item) => item.section_key === this.state.currentSection)) {
                this.state.currentSection = '';
                this.updateListUrl();
            }
            this.renderSectionTabs();
            this.renderSectionIntro();
            this.updateCareerTools();
            this.updateDetailTopbar();
            if (this.state.currentView === 'feed' && this.state.posts.length) {
                const feed = $('[data-blog-feed]', this.shell);
                if (feed) feed.innerHTML = this.state.posts.map((post) => this.postCardHtml(post)).join('');
            }
        }
        const summary = data.summary || {};
        this.setHeroStat('today', summary.today_new_count);
        this.setHeroStat('visible', summary.visible_count);
        this.setHeroStat('comments', summary.comment_count);
        this.setHeroStat('likes', summary.like_count);

        const spotlightPosts = data.spotlight_posts || [];
        const spotlight = $('[data-blog-spotlight]', this.shell);
        if (spotlight) {
            spotlight.innerHTML = spotlightPosts.length
                ? `
                    <div class="blog-spotlight__header">
                        <div>
                            <h2>先看这些</h2>
                            <p>精选、置顶和高讨论内容会优先出现在这里。</p>
                        </div>
                        <button type="button" data-blog-sort="featured">全部精华</button>
                    </div>
                    <div class="blog-spotlight__grid">
                        ${spotlightPosts.map((post, index) => this.spotlightPostHtml(post, index)).join('')}
                    </div>
                `
                : '<div class="blog-discovery-empty">还没有精选内容，发一篇高质量帖子来点亮这里。</div>';
        }

        const trending = $('[data-blog-trending-list]', this.shell);
        if (trending) {
            const posts = data.trending_posts || [];
            trending.innerHTML = posts.length
                ? posts.map((post, index) => this.trendingPostHtml(post, index)).join('')
                : this.railEmptyHtml('还没有形成热议榜');
        }

        const tags = $('[data-blog-hot-tags]', this.shell);
        if (tags) {
            const hotTags = data.hot_tags || [];
            tags.innerHTML = hotTags.length
                ? hotTags.map((tag) => `
                    <button type="button" class="blog-tag blog-tag--hot" data-blog-tag="${escapeHtml(tag.name)}">
                        #${escapeHtml(tag.name)}
                        <span>${formatCompactNumber(tag.count)}</span>
                    </button>
                `).join('')
                : this.railEmptyHtml('暂无热门标签');
        }

        const authors = $('[data-blog-active-authors]', this.shell);
        if (authors) {
            const activeAuthors = data.active_authors || [];
            authors.innerHTML = activeAuthors.length
                ? activeAuthors.map((author) => this.activeAuthorHtml(author)).join('')
                : this.railEmptyHtml('还没有活跃作者');
        }
        this.updateTagFilterBanner();
    }

    setHeroStat(name, value) {
        const node = $(`[data-blog-stat="${name}"]`, this.shell);
        if (node) node.textContent = formatCompactNumber(value || 0);
    }

    spotlightPostHtml(post, index) {
        const cover = this.postCoverMediaHtml(post, 'spotlight', { genericFallback: true });
        return `
            <button type="button" class="blog-spotlight-card${index === 0 ? ' blog-spotlight-card--lead' : ''}" data-blog-open-post="${post.id}">
                <div class="blog-spotlight-card__media">${cover}</div>
                <div class="blog-spotlight-card__body">
                    ${this.sectionBadgeHtml(post.section_key, 'blog-section-badge--spotlight')}
                    <div class="blog-spotlight-card__meta">
                        <span>${escapeHtml(timeAgo(post.created_at))}</span>
                        <span>${post.reading_minutes || 1} 分钟读完</span>
                        <span>${formatCompactNumber(post.hot_score)} 热度</span>
                    </div>
                    <h3>${escapeHtml(post.title || '')}</h3>
                    <p>${escapeHtml(post.summary || '打开看看这篇帖子里发生了什么。')}</p>
                </div>
            </button>
        `;
    }

    trendingPostHtml(post, index) {
        return `
            <button type="button" class="blog-rail-post" data-blog-open-post="${post.id}">
                <span class="blog-rail-post__rank">${index + 1}</span>
                <span class="blog-rail-post__body">
                    <strong>${escapeHtml(post.title || '')}</strong>
                    <span>${formatCompactNumber(post.comment_count)} 评 · ${formatCompactNumber(post.like_count)} 赞 · ${formatCompactNumber(post.hot_score)} 热度</span>
                </span>
            </button>
        `;
    }

    activeAuthorHtml(author = {}) {
        const badge = this.authorCultivationBadgeHtml({
            cultivation_badge: author.cultivation_badge,
            is_anonymous: false,
        }, 'blog-author-cultivation--rank');
        return `
            <button type="button" class="blog-author-rank__item" data-blog-author-posts="${escapeHtml(author.identity || '')}" data-author-name="${escapeHtml(author.display_name || '')}">
                <img src="${escapeHtml(author.avatar_url || '/api/profile/avatar')}" alt="${escapeHtml(author.display_name || '')}" loading="lazy" decoding="async">
                <span class="blog-author-rank__body">
                    <strong>${escapeHtml(author.display_name || '未命名用户')}</strong>
                    <span>${escapeHtml(author.role_label || '')} · ${formatCompactNumber(author.post_count)} 篇 · ${formatCompactNumber(author.hot_score)} 热度</span>
                    ${badge}
                </span>
            </button>
        `;
    }

    railEmptyHtml(text) {
        return `<div class="blog-rail-empty">${escapeHtml(text || '暂无内容')}</div>`;
    }

    async loadFeed({ append = false } = {}) {
        const container = $('[data-blog-feed]', this.shell);
        if (!container) return;
        const controller = this.beginRequest('feed');
        if (!append) {
            container.innerHTML = this.skeletonHtml(3);
        }
        const search = $('[data-blog-search]', this.shell)?.value?.trim() || '';
        const useFollowingFeed = this.state.currentNav === 'following';
        const useOpportunityFeed = !useFollowingFeed && this.state.currentSection === 'career'
            && !this.state.tagFilter
            && !this.state.authorFilter?.identity;
        const endpoint = useFollowingFeed
            ? '/api/blog/following'
            : (useOpportunityFeed ? '/api/blog/opportunities' : '/api/blog/posts');
        const url = new URL(endpoint, window.location.origin);
        if (!useFollowingFeed) url.searchParams.set('sort', this.state.currentSort);
        url.searchParams.set('page', String(this.state.page));
        url.searchParams.set('limit', String(POSTS_PAGE_SIZE));
        if (!useFollowingFeed && !useOpportunityFeed && this.state.currentSection) url.searchParams.set('section', this.state.currentSection);
        if (!useFollowingFeed && search) url.searchParams.set('q', search);
        if (!useFollowingFeed && this.state.tagFilter) url.searchParams.set('tag', this.state.tagFilter);
        if (!useFollowingFeed && this.state.authorFilter?.identity) {
            url.searchParams.set('author', this.state.authorFilter.identity);
        }
        if (useOpportunityFeed) {
            const filters = this.state.careerFilters || {};
            if (filters.region) url.searchParams.set('region', filters.region);
            if (filters.opportunityType) url.searchParams.set('opportunity_type', filters.opportunityType);
            if (filters.deadlineDays) url.searchParams.set('deadline_days', filters.deadlineDays);
            if (filters.userState) url.searchParams.set('state', filters.userState);
        }
        this.updateAuthorFilterBanner();
        this.updateTagFilterBanner();

        try {
            const data = await api.get(`${url.pathname}${url.search}`, { signal: controller.signal });
            const nextPosts = data.posts || [];
            this.state.hasMore = Boolean(data.has_more);
            this.state.posts = append ? [...this.state.posts, ...nextPosts] : nextPosts;
            if (append) {
                container.insertAdjacentHTML('beforeend', nextPosts.map((post) => this.postCardHtml(post)).join(''));
            } else {
                container.innerHTML = this.state.posts.length
                    ? this.state.posts.map((post) => this.postCardHtml(post)).join('')
                    : this.emptyHtml(
                        useFollowingFeed
                            ? '关注板块或作者后，新内容会出现在这里'
                            : (useOpportunityFeed ? '暂时没有符合条件且仍可报名的机会' : '还没有可浏览的帖子'),
                    );
                if (this.state.posts.length) this.animateFeedReveal(container);
            }
            if (append && !nextPosts.length && this.state.page > 1) this.state.page -= 1;
        } catch (error) {
            if (error?.name === 'AbortError') return;
            if (append) {
                if (this.state.page > 1) this.state.page -= 1;
                showToast(error.message || '博客列表加载失败', 'error');
            } else {
                container.innerHTML = this.emptyHtml(error.message || '博客列表加载失败');
            }
        }
        $('[data-blog-load-more]', this.shell)?.toggleAttribute('hidden', !this.state.hasMore);
    }

    async loadMyPosts({ append = false } = {}) {
        const container = $('[data-blog-my-feed]', this.shell);
        if (!container) return;
        if (!append) {
            container.innerHTML = this.skeletonHtml(2);
        }

        const url = new URL('/api/blog/my-posts', window.location.origin);
        url.searchParams.set('page', String(this.state.myPage));
        url.searchParams.set('limit', String(POSTS_PAGE_SIZE));
        if (this.state.myPostsFilter) {
            url.searchParams.set('status', this.state.myPostsFilter);
        }

        try {
            const data = await api.get(`${url.pathname}${url.search}`);
            const nextPosts = data.posts || [];
            this.state.myHasMore = Boolean(data.has_more);
            this.state.myPosts = append ? [...this.state.myPosts, ...nextPosts] : nextPosts;
            if (append) {
                container.insertAdjacentHTML('beforeend', nextPosts.map((post) => this.postCardHtml(post, { ownView: true })).join(''));
            } else {
                container.innerHTML = this.state.myPosts.length
                    ? this.state.myPosts.map((post) => this.postCardHtml(post, { ownView: true })).join('')
                    : this.emptyHtml('你还没有发布过帖子');
            }
            if (append && !nextPosts.length && this.state.myPage > 1) this.state.myPage -= 1;
        } catch (error) {
            if (append) {
                if (this.state.myPage > 1) this.state.myPage -= 1;
                showToast(error.message || '我的帖子加载失败', 'error');
            } else {
                container.innerHTML = this.emptyHtml(error.message || '我的帖子加载失败');
            }
        }
        $('[data-blog-my-load-more]', this.shell)?.toggleAttribute('hidden', !this.state.myHasMore);
    }

    async loadBookmarks({ append = false } = {}) {
        const container = $('[data-blog-bookmarks-feed]', this.shell);
        if (!container) return;
        if (!append) {
            container.innerHTML = this.skeletonHtml(2);
        }

        const url = new URL('/api/blog/bookmarks', window.location.origin);
        url.searchParams.set('page', String(this.state.bmPage));
        url.searchParams.set('limit', String(POSTS_PAGE_SIZE));

        try {
            const data = await api.get(`${url.pathname}${url.search}`);
            const nextPosts = data.posts || [];
            this.state.bmHasMore = Boolean(data.has_more);
            this.state.bookmarkPosts = append ? [...this.state.bookmarkPosts, ...nextPosts] : nextPosts;
            if (append) {
                container.insertAdjacentHTML('beforeend', nextPosts.map((post) => this.postCardHtml(post)).join(''));
            } else {
                container.innerHTML = this.state.bookmarkPosts.length
                    ? this.state.bookmarkPosts.map((post) => this.postCardHtml(post)).join('')
                    : this.emptyHtml('你还没有收藏过帖子');
            }
            if (append && !nextPosts.length && this.state.bmPage > 1) this.state.bmPage -= 1;
        } catch (error) {
            if (append) {
                if (this.state.bmPage > 1) this.state.bmPage -= 1;
                showToast(error.message || '收藏列表加载失败', 'error');
            } else {
                container.innerHTML = this.emptyHtml(error.message || '收藏列表加载失败');
            }
        }
        $('[data-blog-bookmarks-load-more]', this.shell)?.toggleAttribute('hidden', !this.state.bmHasMore);
    }

    async showDetail(postId, { historyMode = 'push', scrollToTop = true } = {}) {
        const container = $('[data-blog-detail-content]', this.shell);
        if (!container || !postId) return;
        const refreshingCurrentDetail = this.state.currentView === 'detail' && this.state.detailPostId === postId;
        if (refreshingCurrentDetail) {
            historyMode = 'none';
            scrollToTop = false;
        }
        if (historyMode === 'push') {
            const fromDetail = this.state.currentView === 'detail' && Number(this.state.detailPostId || 0) > 0;
            const snapshot = fromDetail
                ? (this.lastListSnapshot || window.history.state?.blog?.snapshot || this.buildListSnapshot())
                : this.buildListSnapshot();
            this.lastListSnapshot = snapshot;
            if (!fromDetail) {
                window.history.replaceState(
                    { blog: { view: 'list', postId: null, snapshot } },
                    '',
                    window.location.href,
                );
            }
            const detailUrl = new URL('/blog', window.location.origin);
            if (this.state.currentSection) detailUrl.searchParams.set('section', this.state.currentSection);
            detailUrl.searchParams.set('post', String(postId));
            window.history.pushState(
                { blog: { view: 'detail', postId, snapshot, parentView: fromDetail ? 'detail' : 'list' } },
                '',
                `${detailUrl.pathname}${detailUrl.search}`,
            );
            this.detailHasListHistory = true;
            this.detailPreviousIsPost = fromDetail;
        }
        const controller = this.beginRequest('detail');
        this.showView('detail');
        this.state.detailPostId = postId;
        this.state.detailPost = null;
        this.updateDetailTopbar();
        container.innerHTML = this.skeletonHtml(1);
        if (scrollToTop) window.scrollTo({ top: 0, behavior: 'auto' });

        try {
            const data = await api.get(`/api/blog/posts/${postId}`, { signal: controller.signal });
            const post = data.post;
            this.state.detailPost = post;
            this.updateDetailTopbar(post);
            this.closeCommentPanels();
            this.resetCommentDraft();
            container.innerHTML = this.detailHtml(post);
            this.renderCommentDraftState();
            this.initCommentComposer();
            this.buildDetailToc();
            this.startReadingSession(postId);
        } catch (error) {
            if (error?.name === 'AbortError') return;
            this.state.detailPost = null;
            container.innerHTML = this.emptyHtml(error.message || '帖子详情加载失败');
        }
    }

    currentReadingRatio() {
        const body = $('.blog-detail__body', this.shell);
        if (!body) return 0;
        const rect = body.getBoundingClientRect();
        const visibleBottom = Math.min(window.innerHeight, rect.bottom);
        const consumed = visibleBottom - Math.max(0, rect.top);
        const scrolledPast = Math.max(0, -rect.top);
        return Math.max(0, Math.min((scrolledPast + Math.max(0, consumed)) / Math.max(body.scrollHeight, 1), 1));
    }

    startReadingSession(postId) {
        if (this.readingSession?.postId === postId) return;
        this.stopReadingSession();
        const now = Date.now();
        this.shell.style.setProperty('--blog-reading-progress', '0');
        this.readingSession = {
            postId,
            accumulatedMs: 0,
            visibleSince: document.hidden ? null : now,
            maxRatio: this.currentReadingRatio(),
        };
        window.clearInterval(this.readingProgressTimer);
        this.readingProgressTimer = window.setInterval(() => this.sendReadingProgress(), 15000);
    }

    readingDwellSeconds() {
        const session = this.readingSession;
        if (!session) return 0;
        const activeMs = session.accumulatedMs + (session.visibleSince ? Date.now() - session.visibleSince : 0);
        return Math.max(0, Math.round(activeMs / 1000));
    }

    handleReadingScroll() {
        if (!this.readingSession) return;
        const readingRatio = this.currentReadingRatio();
        this.shell.style.setProperty('--blog-reading-progress', readingRatio.toFixed(4));
        this.readingSession.maxRatio = Math.max(this.readingSession.maxRatio, readingRatio);
        window.clearTimeout(this.readingScrollTimer);
        this.readingScrollTimer = window.setTimeout(() => this.sendReadingProgress(), 2500);
    }

    handleReadingVisibility() {
        const session = this.readingSession;
        if (!session) return;
        if (document.hidden) {
            if (session.visibleSince) session.accumulatedMs += Date.now() - session.visibleSince;
            session.visibleSince = null;
            this.sendReadingProgress();
        } else if (!session.visibleSince) {
            session.visibleSince = Date.now();
        }
    }

    sendReadingProgress({ useBeacon = false } = {}) {
        const session = this.readingSession;
        if (!session) return;
        session.maxRatio = Math.max(session.maxRatio, this.currentReadingRatio());
        const body = JSON.stringify({
            dwell_seconds: this.readingDwellSeconds(),
            max_scroll_ratio: Number(session.maxRatio.toFixed(4)),
        });
        const url = `/api/blog/posts/${session.postId}/reading-progress`;
        if (useBeacon && navigator.sendBeacon) {
            navigator.sendBeacon(url, new Blob([body], { type: 'application/json' }));
            return;
        }
        fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body,
            keepalive: true,
        }).catch(() => {});
    }

    stopReadingSession({ useBeacon = false } = {}) {
        if (!this.readingSession) return;
        if (this.readingSession.visibleSince) {
            this.readingSession.accumulatedMs += Date.now() - this.readingSession.visibleSince;
            this.readingSession.visibleSince = null;
        }
        this.sendReadingProgress({ useBeacon });
        this.readingSession = null;
        window.clearInterval(this.readingProgressTimer);
        window.clearTimeout(this.readingScrollTimer);
        this.readingProgressTimer = null;
        this.readingScrollTimer = null;
    }

    openComposer(post = null) {
        const modal = $('[data-blog-composer-modal]', this.shell);
        if (!modal) return;

        this.composerReturnFocus = document.activeElement;
        this.state.editingPostId = post?.id || null;
        const recovered = this.loadComposerRecovery(this.state.editingPostId);
        const source = recovered ? { ...(post || {}), ...recovered } : (post || {});

        $('[data-blog-compose-title]', this.shell).value = source.title || '';
        $('[data-blog-compose-content]', this.shell).value = source.content_md || '';
        const sourceTags = source.custom_tags || source.tags || [];
        $('[data-blog-compose-tags]', this.shell).value = Array.isArray(sourceTags) ? sourceTags.join(', ') : String(sourceTags || '');
        $('[data-blog-compose-comments]', this.shell).checked = source.allow_comments ?? true;
        this.setSelectedAuthorMode(source.author_display_mode || source.author?.display_mode || 'real_name');

        const visibility = source.visibility || 'public';
        const visibilitySelect = $('[data-blog-compose-visibility]', this.shell);
        if (visibilitySelect) visibilitySelect.value = visibility;
        const sectionSelect = $('[data-blog-compose-section]', this.shell);
        if (sectionSelect) {
            const preferredSection = source.section_key || this.state.currentSection || 'general';
            const canSelectPreferred = Array.from(sectionSelect.options).some((option) => option.value === preferredSection);
            sectionSelect.value = canSelectPreferred ? preferredSection : (sectionSelect.options[0]?.value || 'general');
        }

        this.state.uploadedImages = uniqueMediaItems(source.attachments || source.uploaded_images || []);
        const recoveredUsers = source.selected_users || [];
        this.state.selectedUsers = recoveredUsers.length ? recoveredUsers : (source.visible_user_identities || []).map((identity) => {
            const normalizedIdentity = String(identity || '');
            const cached = this.composeUserMap.get(normalizedIdentity);
            return cached || { identity: normalizedIdentity, name: normalizedIdentity };
        });

        $('[data-blog-composer-title]', this.shell).textContent = post ? '编辑帖子' : '写帖子';
        this.renderImagePreviews();
        this.renderSelectedUsers();
        this.updateVisibilityOptions(visibility, source.visible_class_id || null);
        this.updateAuthorModeHint();
        this.setComposerMode('edit');
        this.updateComposerMetrics();

        modal.hidden = false;
        document.body.classList.add('blog-composer-open');
        const currentFingerprint = this.composerFingerprint();
        this.composeBaseline = recovered ? '' : currentFingerprint;
        this.updateComposerSaveState(recovered ? '已恢复上次未完成的内容' : '内容会自动保存在此设备');
        window.requestAnimationFrame(() => $('[data-blog-compose-title]', this.shell)?.focus());
    }

    closeComposer({ force = false } = {}) {
        const modal = $('[data-blog-composer-modal]', this.shell);
        if (!modal || modal.hidden) return true;
        if (!force && this.isComposerDirty() && !window.confirm('还有未发布的修改，确定关闭编辑器吗？内容会保存在此设备。')) {
            return false;
        }
        if (this.isComposerDirty()) this.saveComposerRecovery();
        if (modal) modal.hidden = true;
        document.body.classList.remove('blog-composer-open');
        window.clearTimeout(this.composeAutosaveTimer);
        this.state.editingPostId = null;
        this.state.uploadedImages = [];
        this.state.selectedUsers = [];
        this.setSelectedAuthorMode('real_name');
        this.updateAuthorModeHint();
        this.renderImagePreviews();
        this.renderSelectedUsers();
        if (this.composerReturnFocus?.isConnected) this.composerReturnFocus.focus();
        this.composerReturnFocus = null;
        return true;
    }

    composerRecoveryKey(editingPostId = this.state.editingPostId) {
        return `lanshare:blog-composer:${this.userIdentity || 'unknown'}:${editingPostId || 'new'}`;
    }

    composerSnapshot() {
        return {
            title: $('[data-blog-compose-title]', this.shell)?.value || '',
            content_md: $('[data-blog-compose-content]', this.shell)?.value || '',
            tags: $('[data-blog-compose-tags]', this.shell)?.value || '',
            section_key: $('[data-blog-compose-section]', this.shell)?.value || 'general',
            visibility: $('[data-blog-compose-visibility]', this.shell)?.value || 'public',
            visible_class_id: $('[data-blog-compose-class]', this.shell)?.value || null,
            allow_comments: Boolean($('[data-blog-compose-comments]', this.shell)?.checked),
            author_display_mode: this.getSelectedAuthorMode(),
            selected_users: this.state.selectedUsers,
            uploaded_images: this.state.uploadedImages,
            saved_at: new Date().toISOString(),
        };
    }

    composerFingerprint() {
        const snapshot = this.composerSnapshot();
        delete snapshot.saved_at;
        return JSON.stringify(snapshot);
    }

    isComposerDirty() {
        return this.composerFingerprint() !== this.composeBaseline;
    }

    loadComposerRecovery(editingPostId = null) {
        try {
            const raw = window.localStorage.getItem(this.composerRecoveryKey(editingPostId));
            if (!raw) return null;
            const parsed = JSON.parse(raw);
            if (!parsed || typeof parsed !== 'object') return null;
            if (!String(parsed.title || '').trim() && !String(parsed.content_md || '').trim()) return null;
            return parsed;
        } catch (error) {
            return null;
        }
    }

    saveComposerRecovery() {
        const modal = $('[data-blog-composer-modal]', this.shell);
        if (!modal || modal.hidden) return;
        try {
            window.localStorage.setItem(this.composerRecoveryKey(), JSON.stringify(this.composerSnapshot()));
            this.updateComposerSaveState(`已自动保存 ${new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`);
        } catch (error) {
            this.updateComposerSaveState('本地自动保存不可用，请及时保存草稿');
        }
    }

    clearComposerRecovery(editingPostId = this.state.editingPostId) {
        try {
            window.localStorage.removeItem(this.composerRecoveryKey(editingPostId));
        } catch (error) {
            // Storage may be unavailable in a restricted browser context.
        }
    }

    scheduleComposerRecovery() {
        const modal = $('[data-blog-composer-modal]', this.shell);
        if (!modal || modal.hidden) return;
        window.clearTimeout(this.composeAutosaveTimer);
        this.updateComposerSaveState('正在保存...');
        this.composeAutosaveTimer = window.setTimeout(() => this.saveComposerRecovery(), 700);
    }

    updateComposerSaveState(text) {
        const node = $('[data-blog-compose-save-state]', this.shell);
        if (node) node.textContent = text || '';
    }

    updateComposerMetrics() {
        const content = $('[data-blog-compose-content]', this.shell)?.value || '';
        const characters = content.replace(/\s/g, '').length;
        const minutes = Math.max(1, Math.ceil(characters / 500));
        const metrics = $('[data-blog-compose-metrics]', this.shell);
        if (metrics) metrics.textContent = `${characters} 字 · 约 ${minutes} 分钟阅读`;
        const preview = $('[data-blog-compose-preview]', this.shell);
        if (preview && !preview.hidden) preview.innerHTML = renderMarkdownHtml(content);
    }

    setComposerMode(mode) {
        const isPreview = mode === 'preview';
        const content = $('[data-blog-compose-content]', this.shell);
        const preview = $('[data-blog-compose-preview]', this.shell);
        if (content) content.hidden = isPreview;
        if (preview) {
            preview.hidden = !isPreview;
            if (isPreview) preview.innerHTML = renderMarkdownHtml(content?.value || '');
        }
        $$('[data-blog-compose-mode]', this.shell).forEach((button) => {
            button.classList.toggle('is-active', button.dataset.blogComposeMode === (isPreview ? 'preview' : 'edit'));
            button.setAttribute('aria-pressed', button.classList.contains('is-active') ? 'true' : 'false');
        });
    }

    getSelectedAuthorMode() {
        const selected = $('[data-blog-compose-author-mode]:checked', this.shell);
        return selected?.value || 'real_name';
    }

    setSelectedAuthorMode(mode = 'real_name') {
        const radios = $$('[data-blog-compose-author-mode]', this.shell);
        if (!radios.length) return;
        const normalizedMode = radios.some((radio) => radio.value === mode && !radio.disabled) ? mode : 'real_name';
        radios.forEach((radio) => {
            radio.checked = radio.value === normalizedMode;
        });
    }

    updateAuthorModeHint(mode = this.getSelectedAuthorMode()) {
        const hint = $('[data-blog-compose-author-mode-hint]', this.shell);
        if (!hint) return;
        if (mode === 'nickname') {
            hint.textContent = this.userNickname
                ? `将以昵称“${this.userNickname}”发帖，并自动附带班级标签和最高宗门修为。`
                : '当前还没有设置昵称，无法使用昵称发帖。';
            return;
        }
        if (mode === 'anonymous') {
            hint.textContent = '将以匿名身份发帖，帖子不会自动附带班级或修为标签，头像也会隐藏为默认样式。';
            return;
        }
        hint.textContent = '默认使用真实名字发布；使用真实名字或昵称时会自动带上班级标签和最高宗门修为。';
    }

    async savePost(status) {
        const title = $('[data-blog-compose-title]', this.shell)?.value?.trim() || '';
        const content = $('[data-blog-compose-content]', this.shell)?.value?.trim() || '';
        const visibility = $('[data-blog-compose-visibility]', this.shell)?.value || 'public';
        const sectionKey = $('[data-blog-compose-section]', this.shell)?.value || 'general';
        const allowComments = Boolean($('[data-blog-compose-comments]', this.shell)?.checked);
        const classIdValue = $('[data-blog-compose-class]', this.shell)?.value || '';
        const authorDisplayMode = this.getSelectedAuthorMode();
        const tags = ($('[data-blog-compose-tags]', this.shell)?.value || '')
            .split(/[,\uff0c]/)
            .map((item) => item.trim())
            .filter(Boolean);

        if (!title) {
            showToast('标题不能为空', 'warning');
            return;
        }
        if (!content) {
            showToast('正文不能为空', 'warning');
            return;
        }

        const payload = {
            title,
            content_md: content,
            section_key: sectionKey,
            visibility,
            allow_comments: allowComments,
            author_display_mode: authorDisplayMode,
            tags,
            status,
        };

        if (visibility === 'class_visible' && classIdValue) {
            payload.visible_class_id = Number(classIdValue);
        }
        if (visibility === 'selected_users') {
            payload.visible_user_identities = this.state.selectedUsers.map((item) => item.identity).filter(Boolean);
        }

        try {
            const editingPostId = this.state.editingPostId;
            if (this.state.editingPostId) {
                await api.put(`/api/blog/posts/${this.state.editingPostId}`, payload);
                showToast('帖子已更新', 'success');
            } else {
                await api.post('/api/blog/posts', payload);
                showToast(status === 'draft' ? '草稿已保存' : '帖子已发布', 'success');
            }

            this.clearComposerRecovery(editingPostId);
            this.composeBaseline = this.composerFingerprint();
            this.closeComposer({ force: true });
            if (this.state.currentView === 'detail' && this.state.detailPostId) {
                await this.showDetail(this.state.detailPostId);
            } else {
                this.refreshCurrentList();
            }
            this.loadDiscovery();
        } catch (error) {
            showToast(error.message || '帖子保存失败', 'error');
        }
    }

    async editPost(postId) {
        try {
            const data = await api.get(`/api/blog/posts/${postId}`);
            this.openComposer(data.post);
        } catch (error) {
            showToast(error.message || '帖子加载失败', 'error');
        }
    }

    async toggleLike(targetType, id) {
        if (!id) return;
        try {
            const data = await api.post(
                targetType === 'post' ? `/api/blog/posts/${id}/like` : `/api/blog/comments/${id}/like`,
                {},
            );
            const selector = targetType === 'post' ? `[data-like-post="${id}"]` : `[data-like-comment="${id}"]`;
            $$(selector, this.shell).forEach((button) => {
                const isLiked = Boolean(data.liked);
                button.classList.toggle('is-active--like', isLiked);
                button.classList.toggle('blog-comment-action--liked', isLiked);
                const icon = $('svg', button);
                if (icon) {
                    icon.outerHTML = isLiked ? SVG.heartFill : SVG.heart;
                }
                const count = $('.blog-interact-btn__count, .blog-comment-action__count, .blog-card-action__count', button);
                if (count) count.textContent = formatCompactNumber(data.like_count ?? 0);
            });
            if (targetType === 'post') {
                $$(`[data-blog-like-count="${id}"]`, this.shell).forEach((node) => {
                    node.textContent = formatCompactNumber(data.like_count ?? 0);
                });
                this.loadDiscovery();
            }
        } catch (error) {
            showToast(error.message || '点赞失败', 'error');
        }
    }

    async toggleBookmark(postId) {
        try {
            const data = await api.post(`/api/blog/posts/${postId}/bookmark`, {});
            $$(`[data-bookmark-post="${postId}"]`, this.shell).forEach((button) => {
                const bookmarked = Boolean(data.bookmarked);
                button.classList.toggle('is-active--bookmark', bookmarked);
                const icon = $('svg', button);
                if (icon) {
                    icon.outerHTML = bookmarked ? SVG.bookmarkFill : SVG.bookmark;
                }
                const count = $('.blog-interact-btn__count, .blog-card-action__count', button);
                if (count) count.textContent = formatCompactNumber(data.bookmark_count ?? 0);
            });
            $$(`[data-blog-bookmark-count="${postId}"]`, this.shell).forEach((node) => {
                node.textContent = formatCompactNumber(data.bookmark_count ?? 0);
            });
            this.loadDiscovery();
        } catch (error) {
            showToast(error.message || '收藏失败', 'error');
        }
    }

    async setOpportunityState(opportunityId, state) {
        if (!opportunityId) return;
        const normalizedState = state || 'none';
        try {
            const data = await api.post(`/api/blog/opportunities/${opportunityId}/state`, {
                state: normalizedState,
            });
            const nextState = data.state || '';
            $$(`[data-blog-opportunity-save="${opportunityId}"]`, this.shell).forEach((button) => {
                button.dataset.currentState = nextState;
                button.textContent = nextState ? (OPPORTUNITY_STATE_LABELS[nextState] || '已跟进') : '收藏机会';
            });
            $$(`[data-blog-opportunity-state="${opportunityId}"]`, this.shell).forEach((select) => {
                select.value = nextState;
            });
            const updatePost = (post) => {
                if (post?.opportunity?.id === opportunityId) post.opportunity.user_state = nextState || null;
            };
            this.state.posts.forEach(updatePost);
            updatePost(this.state.detailPost);
            showToast(nextState ? `求职进度已更新为“${OPPORTUNITY_STATE_LABELS[nextState] || nextState}”` : '已移出求职清单', 'success');
            if (this.state.careerFilters?.userState) this.loadFeed();
        } catch (error) {
            showToast(error.message || '求职进度更新失败', 'error');
        }
    }

    openReport(targetType, targetId) {
        const dialog = $('[data-blog-report-dialog]', this.shell);
        if (!dialog || !targetId) return;
        this.reportTarget = { targetType, targetId };
        const details = $('[data-blog-report-details]', dialog);
        if (details) details.value = '';
        if (typeof dialog.showModal === 'function') dialog.showModal();
    }

    async submitReport(event) {
        event.preventDefault();
        if (!this.reportTarget) return;
        const dialog = $('[data-blog-report-dialog]', this.shell);
        const submit = event.currentTarget?.querySelector('button[type="submit"]');
        if (submit) submit.disabled = true;
        try {
            await api.post('/api/blog/reports', {
                target_type: this.reportTarget.targetType,
                target_id: this.reportTarget.targetId,
                reason_code: $('[data-blog-report-reason]', dialog)?.value || 'other',
                details: $('[data-blog-report-details]', dialog)?.value || '',
            });
            dialog?.close();
            this.reportTarget = null;
            showToast('反馈已提交，内容管理人员会进行核验', 'success');
        } catch (error) {
            showToast(error.message || '反馈提交失败', 'error');
        } finally {
            if (submit) submit.disabled = false;
        }
    }

    async togglePin(postId) {
        try {
            const data = await api.post(`/api/blog/posts/${postId}/pin`, {});
            showToast(data.is_pinned ? '已置顶' : '已取消置顶', 'success');
            await this.refreshAfterDetailMutation(postId);
            this.loadDiscovery();
        } catch (error) {
            showToast(error.message || '置顶操作失败', 'error');
        }
    }

    async toggleFeature(postId) {
        try {
            const data = await api.post(`/api/blog/posts/${postId}/feature`, {});
            showToast(data.is_featured ? '已设为精华' : '已取消精华', 'success');
            await this.refreshAfterDetailMutation(postId);
            this.loadDiscovery();
        } catch (error) {
            showToast(error.message || '精华操作失败', 'error');
        }
    }

    async toggleVisibility(postId) {
        if (!window.confirm('确定调整这篇帖子的可见状态？')) return;

        try {
            const data = await api.post(`/api/blog/posts/${postId}/hide`, { reason: '' });
            showToast(data.status === 'moderated' ? '帖子已转为私密' : '帖子已恢复可见', 'success');
            await this.refreshAfterDetailMutation(postId);
            this.loadDiscovery();
        } catch (error) {
            showToast(error.message || '可见性操作失败', 'error');
        }
    }

    async toggleComments(postId) {
        try {
            const data = await api.post(`/api/blog/posts/${postId}/comments-toggle`, {});
            showToast(data.allow_comments ? '评论已开启' : '评论已关闭', 'success');
            await this.refreshAfterDetailMutation(postId);
        } catch (error) {
            showToast(error.message || '评论状态更新失败', 'error');
        }
    }

    async deletePost(postId) {
        if (!window.confirm('确定删除这篇帖子？此操作不可撤销。')) return;
        try {
            await api.del(`/api/blog/posts/${postId}`);
            showToast('帖子已删除', 'success');
            this.state.detailPostId = null;
            this.showCurrentListView();
            this.refreshCurrentList();
            this.loadDiscovery();
            this.updateListUrl();
        } catch (error) {
            showToast(error.message || '删除失败', 'error');
        }
    }

    async deleteComment(commentId) {
        if (!window.confirm('确定删除这条评论？')) return;
        try {
            await api.del(`/api/blog/comments/${commentId}`);
            showToast('评论已删除', 'success');
            if (this.state.detailPostId) {
                await this.showDetail(this.state.detailPostId);
            }
            this.loadDiscovery();
        } catch (error) {
            showToast(error.message || '删除评论失败', 'error');
        }
    }

    startReply(commentId, authorName) {
        const input = $('[data-blog-comment-input]', this.shell);
        if (!input) return;
        this.closeCommentPanels();
        this.commentDraft.replyTo = commentId;
        this.commentDraft.replyName = authorName;
        input.focus();
        input.scrollIntoView({ behavior: 'smooth', block: 'center' });
        this.autoSizeCommentInput(input);
        this.renderCommentDraftState();
    }

    async submitComment() {
        if (!this.state.detailPostId) return;
        const input = $('[data-blog-comment-input]', this.shell);
        if (!input) return;
        const content = input.value.trim();
        const attachments = uniqueMediaItems(this.commentDraft.attachments);
        const customEmojis = uniqueMediaItems(this.commentDraft.customEmojis);

        if (!content && !attachments.length && !customEmojis.length) {
            showToast('评论内容不能为空', 'warning');
            return;
        }

        try {
            await api.post(`/api/blog/posts/${this.state.detailPostId}/comments`, {
                content_md: content,
                parent_comment_id: this.commentDraft.replyTo,
                attachments_json: JSON.stringify(attachments.map((item) => ({
                    file_hash: normalizeFileHash(item),
                    name: item.name || item.filename || '图片',
                }))),
                emoji_payload_json: JSON.stringify(customEmojis.map((item) => ({
                    file_hash: normalizeFileHash(item),
                    name: item.name || '自定义表情',
                }))),
            });
            showToast('评论已发布', 'success');
            this.resetCommentDraft({ keepPicker: false });
            input.value = '';
            this.autoSizeCommentInput(input);
            this.closeCommentPanels();
            await this.showDetail(this.state.detailPostId);
            this.loadDiscovery();
        } catch (error) {
            showToast(error.message || '评论失败', 'error');
        }
    }

    async handleImageUpload(fileList, { context }) {
        const files = [...(fileList || [])];
        if (!files.length) return;

        for (const file of files) {
            if (!file.type.startsWith('image/')) {
                showToast(`文件 ${file.name} 不是图片`, 'warning');
                continue;
            }
            const formData = new FormData();
            formData.append('file', file, file.name);
            try {
                const data = await api.upload('/api/blog/upload-image', formData);
                const asset = data.file;
                if (!asset) continue;

                if (context === 'compose') {
                    this.state.uploadedImages = uniqueMediaItems([...this.state.uploadedImages, asset]);
                    this.insertComposeImageMarkdown(asset);
                    this.renderImagePreviews();
                } else if (context === 'comment') {
                    this.commentDraft.attachments = uniqueMediaItems([...this.commentDraft.attachments, asset]);
                    this.renderCommentDraftState();
                }
            } catch (error) {
                showToast(error.message || `上传 ${file.name} 失败`, 'error');
            }
        }

        if (context === 'compose') {
            const input = $('[data-blog-compose-file-input]', this.shell);
            if (input) input.value = '';
            return;
        }
        const commentInput = $('[data-blog-comment-file-input]', this.shell);
        if (commentInput) commentInput.value = '';
    }

    insertComposeImageMarkdown(asset) {
        const textarea = $('[data-blog-compose-content]', this.shell);
        if (!textarea) return;
        const markdown = `![${asset.filename || asset.name || '图片'}](${asset.url})`;
        const start = textarea.selectionStart ?? textarea.value.length;
        const end = textarea.selectionEnd ?? textarea.value.length;
        const prefix = textarea.value && !textarea.value.endsWith('\n') ? '\n' : '';
        const suffix = textarea.value.slice(end).startsWith('\n') ? '' : '\n';
        textarea.value = `${textarea.value.slice(0, start)}${prefix}${markdown}${suffix}${textarea.value.slice(end)}`;
        const nextCursor = start + prefix.length + markdown.length + suffix.length;
        textarea.focus();
        textarea.setSelectionRange(nextCursor, nextCursor);
    }

    removeComposeImage(index) {
        const asset = this.state.uploadedImages[index];
        if (!asset) return;
        this.state.uploadedImages.splice(index, 1);
        const textarea = $('[data-blog-compose-content]', this.shell);
        if (textarea) {
            const url = String(asset.url || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
            const name = escapeHtml(asset.filename || asset.name || '图片');
            const pattern = new RegExp(`\\n?!\\[[^\\]]*\\]\\(${url}\\)\\n?`, 'i');
            textarea.value = textarea.value.replace(pattern, '\n').replace(/\n{3,}/g, '\n\n').trim();
        }
        this.renderImagePreviews();
    }

    removeCommentAttachment(index) {
        this.commentDraft.attachments.splice(index, 1);
        this.renderCommentDraftState();
    }

    removeCommentEmoji(index) {
        this.commentDraft.customEmojis.splice(index, 1);
        this.renderCommentDraftState();
    }

    renderImagePreviews() {
        const container = $('[data-blog-compose-images]', this.shell);
        if (!container) return;
        container.innerHTML = this.state.uploadedImages.map((item, index) => `
            <div class="blog-image-preview__item">
                <img src="${escapeHtml(item.url || '')}" alt="${escapeHtml(item.filename || item.name || '图片')}">
                <button type="button" class="blog-image-preview__remove" data-remove-image="${index}" aria-label="移除图片">×</button>
            </div>
        `).join('');
    }

    insertMarkdown(type) {
        const textarea = $('[data-blog-compose-content]', this.shell);
        if (!textarea) return;
        const start = textarea.selectionStart ?? 0;
        const end = textarea.selectionEnd ?? 0;
        const selected = textarea.value.slice(start, end);
        let insertion = '';

        switch (type) {
        case 'bold':
            insertion = `**${selected || '加粗文字'}**`;
            break;
        case 'italic':
            insertion = `*${selected || '强调内容'}*`;
            break;
        case 'code':
            insertion = `\`${selected || 'code'}\``;
            break;
        case 'codeblock':
            insertion = `\n\`\`\`\n${selected || '// code'}\n\`\`\`\n`;
            break;
        case 'quote':
            insertion = `\n> ${selected || '引用内容'}\n`;
            break;
        case 'ul':
            insertion = `\n- ${selected || '列表项'}\n`;
            break;
        case 'ol':
            insertion = `\n1. ${selected || '列表项'}\n`;
            break;
        case 'h2':
            insertion = `\n## ${selected || '小标题'}\n`;
            break;
        case 'link':
            insertion = `[${selected || '链接文字'}](https://)`;
            break;
        case 'image':
            $('[data-blog-compose-file-input]', this.shell)?.click();
            return;
        default:
            return;
        }

        textarea.value = `${textarea.value.slice(0, start)}${insertion}${textarea.value.slice(end)}`;
        const nextPosition = start + insertion.length;
        textarea.focus();
        textarea.setSelectionRange(nextPosition, nextPosition);
    }

    async updateVisibilityOptions(visibility, selectedClassId = null) {
        const classWrap = $('[data-blog-compose-class-wrap]', this.shell);
        const usersWrap = $('[data-blog-compose-users-config]', this.shell);
        if (classWrap) classWrap.hidden = visibility !== 'class_visible';
        if (usersWrap) usersWrap.hidden = visibility !== 'selected_users';

        if (visibility === 'class_visible') {
            await this.loadClasses(selectedClassId);
            return;
        }
        const classSelect = $('[data-blog-compose-class]', this.shell);
        if (classSelect && selectedClassId) classSelect.value = String(selectedClassId);
    }

    async loadClasses(selectedClassId = null) {
        try {
            const data = await api.get('/api/blog/user-classes');
            const select = $('[data-blog-compose-class]', this.shell);
            if (!select) return;
            select.innerHTML = (data.classes || []).map((item) => (
                `<option value="${item.id}">${escapeHtml(item.name)}</option>`
            )).join('');
            this.state.composeClassesLoaded = true;
            if (selectedClassId) select.value = String(selectedClassId);
        } catch (error) {
            console.error(error);
        }
    }

    async searchUsers(keyword) {
        const container = $('[data-blog-compose-user-results]', this.shell);
        if (!container) return;
        if (!keyword) {
            container.innerHTML = '';
            return;
        }

        const url = new URL('/api/blog/users-search', window.location.origin);
        url.searchParams.set('q', keyword);
        const selectedClassId = $('[data-blog-compose-class]', this.shell)?.value;
        if (selectedClassId) {
            url.searchParams.set('class_id', selectedClassId);
        }

        try {
            const data = await api.get(`${url.pathname}${url.search}`);
            container.innerHTML = (data.users || []).slice(0, 12).map((user) => {
                this.composeUserMap.set(user.identity, user);
                const exists = this.state.selectedUsers.some((item) => item.identity === user.identity);
                return `
                    <button type="button" class="blog-user-chip blog-user-chip--pickable${exists ? ' is-active' : ''}" data-pick-user="${escapeHtml(user.identity)}">
                        <span>${escapeHtml(user.name || user.nickname || user.identity)}</span>
                        <span class="blog-user-chip__meta">${escapeHtml(user.role_label || '')}${user.class_name ? ` · ${escapeHtml(user.class_name)}` : ''}</span>
                    </button>
                `;
            }).join('');
        } catch (error) {
            container.innerHTML = '';
        }
    }

    pickUser(identity) {
        if (!identity) return;
        if (this.state.selectedUsers.some((item) => item.identity === identity)) {
            return;
        }
        const cached = this.composeUserMap.get(identity) || { identity, name: identity, role_label: '' };
        this.state.selectedUsers.push(cached);
        this.renderSelectedUsers();
        const searchInput = $('[data-blog-compose-user-search]', this.shell);
        const results = $('[data-blog-compose-user-results]', this.shell);
        if (searchInput) searchInput.value = '';
        if (results) results.innerHTML = '';
    }

    renderSelectedUsers() {
        const container = $('[data-blog-compose-selected-users]', this.shell);
        if (!container) return;
        container.innerHTML = this.state.selectedUsers.map((user, index) => `
            <span class="blog-user-chip">
                ${escapeHtml(user.name || user.identity)}
                <span class="blog-user-chip__meta">${escapeHtml(user.role_label || '')}</span>
                <button type="button" class="blog-user-chip__remove" data-remove-user="${index}" aria-label="移除用户">×</button>
            </span>
        `).join('');
    }

    async ensureCustomEmojiLibrary() {
        try {
            const data = await api.get('/api/blog/custom-emojis?limit=80');
            this.state.customEmojiLibrary = data.emojis || [];
        } catch (error) {
            this.state.customEmojiLibrary = [];
        }
        this.renderCommentCustomEmojiPanel();
    }

    autoSizeCommentInput(input = $('[data-blog-comment-input]', this.shell)) {
        if (!input) return;
        input.style.height = 'auto';
        input.style.height = `${Math.min(input.scrollHeight, 260)}px`;
    }

    closeCommentPanels() {
        const customPanel = $('[data-blog-comment-custom-emoji-panel]', this.shell);
        if (customPanel) customPanel.hidden = true;
        if (this.commentDraft.emojiPicker?.isOpen?.()) {
            this.commentDraft.emojiPicker.close();
        }
        $('[data-blog-comment-custom-emoji-toggle]', this.shell)?.classList.remove('is-active');
        $('[data-blog-comment-emoji-toggle]', this.shell)?.classList.remove('is-active');
    }

    initCommentComposer() {
        const input = $('[data-blog-comment-input]', this.shell);
        if (!input) return;
        const anchor = $('[data-blog-comment-emoji-anchor]', this.shell);
        if (anchor) {
            const picker = createEmojiPicker({ targetInput: input });
            anchor.innerHTML = '';
            anchor.appendChild(picker.element);
            this.commentDraft.emojiPicker = picker;
        }
        this.autoSizeCommentInput(input);
        this.renderCommentCustomEmojiPanel();
    }

    toggleCommentEmojiPicker() {
        if (!this.commentDraft.emojiPicker) return;
        $('[data-blog-comment-custom-emoji-panel]', this.shell)?.setAttribute('hidden', 'hidden');
        $('[data-blog-comment-custom-emoji-toggle]', this.shell)?.classList.remove('is-active');
        this.commentDraft.emojiPicker.toggle();
        $('[data-blog-comment-emoji-toggle]', this.shell)?.classList.toggle(
            'is-active',
            Boolean(this.commentDraft.emojiPicker?.isOpen?.()),
        );
    }

    toggleCustomEmojiPanel() {
        this.commentDraft.emojiPicker?.close?.();
        $('[data-blog-comment-emoji-toggle]', this.shell)?.classList.remove('is-active');
        const panel = $('[data-blog-comment-custom-emoji-panel]', this.shell);
        if (!panel) return;
        panel.hidden = !panel.hidden;
        $('[data-blog-comment-custom-emoji-toggle]', this.shell)?.classList.toggle('is-active', !panel.hidden);
    }

    renderCommentCustomEmojiPanel() {
        const panel = $('[data-blog-comment-custom-emoji-panel]', this.shell);
        if (!panel) return;
        if (!this.state.customEmojiLibrary.length) {
            panel.innerHTML = `
                <div class="blog-comment-custom-emoji-panel__header">
                    <div class="blog-comment-custom-emoji-panel__title">自定义表情</div>
                    <button type="button" class="blog-comment-custom-emoji-panel__close" data-blog-close-custom-emoji-panel>×</button>
                </div>
                <div class="blog-comment-panel-empty">还没有可用的自定义表情</div>
            `;
            return;
        }
        panel.innerHTML = `
            <div class="blog-comment-custom-emoji-panel__header">
                <div class="blog-comment-custom-emoji-panel__title">自定义表情</div>
                <button type="button" class="blog-comment-custom-emoji-panel__close" data-blog-close-custom-emoji-panel>×</button>
            </div>
            <div class="blog-comment-custom-emoji-panel__grid">
                ${this.state.customEmojiLibrary.map((item) => `
                    <button type="button" class="blog-custom-emoji-picker__item" data-blog-custom-emoji-pick data-file-hash="${escapeHtml(item.file_hash)}" title="${escapeHtml(item.name || '自定义表情')}">
                        <img src="${escapeHtml(item.image_url || '')}" alt="${escapeHtml(item.name || '自定义表情')}" loading="lazy" decoding="async">
                    </button>
                `).join('')}
            </div>
        `;
    }

    insertCommentSnippet(type) {
        const input = $('[data-blog-comment-input]', this.shell);
        if (!input) return;
        const start = input.selectionStart ?? input.value.length;
        const end = input.selectionEnd ?? input.value.length;
        const selected = input.value.slice(start, end);
        let insertion = '';

        switch (type) {
        case 'quote':
            insertion = `\n> ${selected || '补充引用'}\n`;
            break;
        case 'code':
            insertion = `\`${selected || 'code'}\``;
            break;
        case 'codeblock':
            insertion = `\n\`\`\`\n${selected || '// 在这里贴代码'}\n\`\`\`\n`;
            break;
        case 'mention-housekeeper':
            insertion = `${start > 0 && !/\s$/.test(input.value.slice(0, start)) ? ' ' : ''}@管家 `;
            break;
        default:
            return;
        }

        input.value = `${input.value.slice(0, start)}${insertion}${input.value.slice(end)}`;
        const nextPosition = start + insertion.length;
        input.focus();
        input.setSelectionRange(nextPosition, nextPosition);
        this.autoSizeCommentInput(input);
    }

    addCommentCustomEmoji(fileHash) {
        const emoji = this.state.customEmojiLibrary.find((item) => item.file_hash === fileHash);
        if (!emoji) return;
        this.commentDraft.customEmojis = uniqueMediaItems([...this.commentDraft.customEmojis, emoji]);
        this.renderCommentDraftState();
        this.closeCommentPanels();
    }

    resetCommentDraft({ keepPicker = false } = {}) {
        const picker = keepPicker ? this.commentDraft.emojiPicker : null;
        this.commentDraft = this.createEmptyCommentDraft();
        if (picker) {
            this.commentDraft.emojiPicker = picker;
        }
    }

    renderCommentDraftState() {
        const replyBanner = $('[data-blog-replying]', this.shell);
        const cancelButton = $('[data-blog-reply-cancel]', this.shell);
        if (replyBanner) {
            replyBanner.hidden = !this.commentDraft.replyTo;
            replyBanner.innerHTML = this.commentDraft.replyTo
                ? `正在回复 <strong>${escapeHtml(this.commentDraft.replyName || '')}</strong>`
                : '';
        }
        if (cancelButton) cancelButton.hidden = !this.commentDraft.replyTo;

        const emojiPreview = $('[data-blog-comment-custom-emoji-preview]', this.shell);
        if (emojiPreview) {
            emojiPreview.innerHTML = this.commentDraft.customEmojis.map((item, index) => `
                <div class="blog-comment-media-chip">
                    <img src="${escapeHtml(item.image_url || '')}" alt="${escapeHtml(item.name || '自定义表情')}" loading="lazy" decoding="async">
                    <button type="button" data-remove-comment-emoji="${index}" aria-label="移除表情">×</button>
                </div>
            `).join('');
            emojiPreview.hidden = !this.commentDraft.customEmojis.length;
        }

        const attachmentPreview = $('[data-blog-comment-attachment-preview]', this.shell);
        if (attachmentPreview) {
            attachmentPreview.innerHTML = this.commentDraft.attachments.map((item, index) => `
                <div class="blog-comment-media-chip blog-comment-media-chip--image">
                    <img src="${escapeHtml(item.url || '')}" alt="${escapeHtml(item.filename || item.name || '图片')}" loading="lazy" decoding="async">
                    <button type="button" data-remove-comment-attachment="${index}" aria-label="移除图片">×</button>
                </div>
            `).join('');
            attachmentPreview.hidden = !this.commentDraft.attachments.length;
        }
    }

    teardownDetailToc() {
        this.tocObserver?.disconnect();
        this.tocObserver = null;
        const toc = $('[data-blog-toc]', this.shell);
        if (toc) toc.hidden = true;
        $('[data-blog-toc-list]', this.shell)?.replaceChildren();
    }

    buildDetailToc() {
        this.teardownDetailToc();
        const toc = $('[data-blog-toc]', this.shell);
        const list = $('[data-blog-toc-list]', this.shell);
        const body = $('.blog-detail__body', this.shell);
        if (!toc || !list || !body) return;
        const headings = $$('h2, h3', body).filter((node) => node.textContent.trim());
        if (headings.length < 2) return;
        list.innerHTML = headings.map((heading, index) => {
            if (!heading.id) heading.id = `blog-toc-${index}`;
            return `<a class="blog-toc__item blog-toc__item--${heading.tagName.toLowerCase()}" href="#${escapeHtml(heading.id)}" data-blog-toc-target="${escapeHtml(heading.id)}">${escapeHtml(heading.textContent.trim())}</a>`;
        }).join('');
        this.tocObserver = new IntersectionObserver((entries) => {
            entries.forEach((entry) => {
                if (!entry.isIntersecting) return;
                $$('[data-blog-toc-target]', list).forEach((link) => {
                    link.classList.toggle('is-active', link.dataset.blogTocTarget === entry.target.id);
                });
            });
        }, { rootMargin: '-15% 0px -65% 0px' });
        headings.forEach((heading) => this.tocObserver.observe(heading));
        toc.hidden = false;
    }

    refreshCurrentList() {
        if (this.state.currentNav === 'my-posts') {
            this.state.myPage = 1;
            this.loadMyPosts();
            return;
        }
        if (this.state.currentNav === 'bookmarks') {
            this.state.bmPage = 1;
            this.loadBookmarks();
            return;
        }
        this.state.page = 1;
        this.loadFeed();
    }

    async refreshAfterDetailMutation(postId) {
        if (this.state.detailPostId === postId) {
            await this.showDetail(postId);
            return;
        }
        this.refreshCurrentList();
    }

    canOpenUserPopover(author = {}) {
        if (!author || author.is_anonymous) return false;
        const role = String(author.role || '').trim().toLowerCase();
        const identity = String(author.identity || '').trim();
        return Boolean(identity && role && role !== 'assistant');
    }

    userMenuAttrs(author = {}) {
        if (!this.canOpenUserPopover(author)) return '';
        return [
            'data-blog-user-menu',
            `data-user-identity="${escapeHtml(author.identity || '')}"`,
            `data-user-role="${escapeHtml(author.role || '')}"`,
            `data-user-name="${escapeHtml(author.display_name || '')}"`,
        ].join(' ');
    }

    authorAvatarHtml(author = {}, className = '', fallbackUrl = this.currentAvatarUrl) {
        const avatar = `<img class="${escapeHtml(className)}" src="${escapeHtml(author.avatar_url || fallbackUrl)}" alt="${escapeHtml(author.display_name || '')}">`;
        if (!this.canOpenUserPopover(author)) return avatar;
        return `<button type="button" class="blog-user-link blog-user-link--avatar" ${this.userMenuAttrs(author)}>${avatar}</button>`;
    }

    authorNameHtml(author = {}, className = '') {
        const name = escapeHtml(author.display_name || '');
        if (!this.canOpenUserPopover(author)) return `<span class="${escapeHtml(className)}">${name}</span>`;
        return `<button type="button" class="blog-user-link blog-user-link--name ${escapeHtml(className)}" ${this.userMenuAttrs(author)}>${name}</button>`;
    }

    authorCultivationBadgeHtml(author = {}, className = '') {
        const badge = author?.cultivation_badge || null;
        const label = String(badge?.label || '').trim();
        if (!label || author?.is_anonymous) return '';
        const theme = String(badge?.theme || 'mortal').replace(/[^a-z0-9_-]/gi, '') || 'mortal';
        const title = `${label} · 修为 ${badge?.score ?? 0}`;
        return `<span class="blog-author-cultivation ${escapeHtml(className)}" data-theme="${escapeHtml(theme)}" title="${escapeHtml(title)}">${escapeHtml(label)}</span>`;
    }

    postCardHtml(post, { ownView = false } = {}) {
        const badges = [];
        const sectionBadge = this.sectionBadgeHtml(post.section_key, 'blog-section-badge--card');
        if (sectionBadge) badges.push(sectionBadge);
        if (post.is_pinned) badges.push('<span class="blog-badge blog-badge--pin">置顶</span>');
        if (post.is_featured) badges.push('<span class="blog-badge blog-badge--feature">精华</span>');
        if (post.status === 'draft') badges.push('<span class="blog-badge blog-badge--draft">草稿</span>');
        if (post.status === 'moderated') badges.push('<span class="blog-badge blog-badge--moderated">私密</span>');
        if (post.visibility !== 'public') badges.push(`<span class="blog-badge blog-badge--visibility">${escapeHtml(post.visibility_label || '权限可见')}</span>`);

        const tags = (post.tags || []).map((tag) => (
            `<button type="button" class="blog-tag" data-blog-tag="${escapeHtml(tag)}">${escapeHtml(tag)}</button>`
        )).join('');
        const coverMedia = this.postCoverMediaHtml(post, 'card');
        const cover = coverMedia ? `<div class="blog-post-card__media">${coverMedia}</div>` : '';
        const likedClass = post.is_liked ? ' is-active--like' : '';
        const bookmarkedClass = post.is_bookmarked ? ' is-active--bookmark' : '';
        const postUrl = new URL('/blog', window.location.origin);
        if (post.section_key) postUrl.searchParams.set('section', post.section_key);
        postUrl.searchParams.set('post', String(post.id));

        return `
            <article class="blog-post-card${cover ? ' blog-post-card--with-cover' : ''}${post.is_pinned ? ' is-pinned' : ''}${post.is_featured ? ' is-featured' : ''}" data-blog-post-id="${post.id}" aria-labelledby="blog-post-title-${post.id}">
                <div class="blog-post-card__content">
                    ${badges.length ? `<div class="blog-post-card__badges">${badges.join('')}</div>` : ''}
                    <div class="blog-post-card__signal">
                        <span>${post.reading_minutes || 1} 分钟读完</span>
                        <span>${formatCompactNumber(post.hot_score)} 热度</span>
                    </div>
                    <h3 class="blog-post-card__title" id="blog-post-title-${post.id}"><a href="${escapeHtml(`${postUrl.pathname}${postUrl.search}`)}" data-blog-open-post="${post.id}">${escapeHtml(post.title || '')}</a></h3>
                    <p class="blog-post-card__summary">${escapeHtml(post.summary || '')}</p>
                    ${this.opportunityCardHtml(post.opportunity)}
                    ${tags ? `<div class="blog-post-card__tags">${tags}</div>` : ''}
                    <div class="blog-post-card__meta">
                        <div class="blog-post-card__author">
                            ${this.authorAvatarHtml(post.author, 'blog-post-card__avatar')}
                            ${this.authorNameHtml(post.author, 'blog-post-card__author-name')}
                            ${this.authorCultivationBadgeHtml(post.author)}
                            <span class="blog-post-card__author-role">${escapeHtml(ROLE_LABELS[post.author?.role] || '')}</span>
                            <span class="blog-post-card__time">${escapeHtml(timeAgo(post.created_at))}</span>
                        </div>
                        <div class="blog-post-card__stats">
                            <span class="blog-stat">${SVG.eye}<span>${formatCompactNumber(post.view_count)}</span></span>
                            <span class="blog-stat">${SVG.comment}<span>${formatCompactNumber(post.comment_count)}</span></span>
                        </div>
                    </div>
                    <div class="blog-post-card__actions">
                        <button type="button" class="blog-card-action${likedClass}" data-like-post="${post.id}">
                            ${post.is_liked ? SVG.heartFill : SVG.heart}
                            <span>点赞</span>
                            <span class="blog-card-action__count" data-blog-like-count="${post.id}">${formatCompactNumber(post.like_count)}</span>
                        </button>
                        <button type="button" class="blog-card-action${bookmarkedClass}" data-bookmark-post="${post.id}">
                            ${post.is_bookmarked ? SVG.bookmarkFill : SVG.bookmark}
                            <span>收藏</span>
                            <span class="blog-card-action__count" data-blog-bookmark-count="${post.id}">${formatCompactNumber(post.bookmark_count)}</span>
                        </button>
                    </div>
                    ${ownView ? `<div class="blog-post-card__footnote">状态：${escapeHtml(this.statusLabel(post.status))}</div>` : ''}
                </div>
                ${cover}
            </article>
        `;
    }

    statusLabel(status) {
        if (status === 'draft') return '草稿';
        if (status === 'moderated') return '私密';
        if (status === 'hidden') return '隐藏';
        return '已发布';
    }

    detailHtml(post) {
        const permissions = post.permissions || {};
        const metaBadges = [];
        const sectionBadge = this.sectionBadgeHtml(post.section_key, 'blog-section-badge--detail');
        if (sectionBadge) metaBadges.push(sectionBadge);
        if (post.is_pinned) metaBadges.push('<span class="blog-badge blog-badge--pin">置顶</span>');
        if (post.is_featured) metaBadges.push('<span class="blog-badge blog-badge--feature">精华</span>');
        if (post.status === 'draft') metaBadges.push('<span class="blog-badge blog-badge--draft">草稿</span>');
        if (post.status === 'moderated') metaBadges.push('<span class="blog-badge blog-badge--moderated">私密</span>');
        if (post.visibility !== 'public') metaBadges.push(`<span class="blog-badge blog-badge--visibility">${escapeHtml(post.visibility_label || '权限可见')}</span>`);

        const actionButtons = [
            permissions.can_edit ? `<button type="button" class="blog-action-btn" data-edit-post="${post.id}">${SVG.edit}<span>编辑</span></button>` : '',
            permissions.can_toggle_comments ? `<button type="button" class="blog-action-btn" data-toggle-comments="${post.id}">${post.allow_comments ? '关闭评论' : '开启评论'}</button>` : '',
            permissions.can_pin ? `<button type="button" class="blog-action-btn blog-action-btn--warning" data-pin-post="${post.id}">${post.is_pinned ? '取消置顶' : '置顶'}</button>` : '',
            permissions.can_feature ? `<button type="button" class="blog-action-btn blog-action-btn--warning" data-feature-post="${post.id}">${post.is_featured ? '取消精华' : '设为精华'}</button>` : '',
            permissions.can_hide ? `<button type="button" class="blog-action-btn blog-action-btn--warning" data-hide-post="${post.id}">${post.status === 'moderated' ? '恢复可见' : '转为私密'}</button>` : '',
            permissions.can_delete ? `<button type="button" class="blog-action-btn blog-action-btn--danger" data-delete-post="${post.id}">删除</button>` : '',
            `<button type="button" class="blog-action-btn" data-blog-report-post="${post.id}">反馈问题</button>`,
        ].filter(Boolean).join('');

        const tags = (post.tags || []).map((tag) => (
            `<button type="button" class="blog-tag" data-blog-tag="${escapeHtml(tag)}">${escapeHtml(tag)}</button>`
        )).join('');
        const editorialCover = post.cover_image_kind === 'editorial'
            ? `<div class="blog-detail__editorial-cover">${this.postCoverFallbackHtml(post, 'detail')}</div>`
            : '';

        return `
            <article class="blog-detail">
                ${metaBadges.length ? `<div class="blog-detail__badges">${metaBadges.join('')}</div>` : ''}
                <h1 class="blog-detail__title">${escapeHtml(post.title || '')}</h1>
                <div class="blog-detail__reading-meta">
                    <span>${post.reading_minutes || 1} 分钟读完</span>
                    <span>${formatCompactNumber(post.hot_score)} 热度</span>
                    <span>${formatCompactNumber(post.comment_count)} 条讨论</span>
                    <span>${formatCompactNumber(post.bookmark_count)} 人收藏</span>
                </div>
                ${this.opportunityDetailHtml(post.opportunity)}
                <div class="blog-detail__author-row">
                    <div class="blog-detail__author">
                        ${this.authorAvatarHtml(post.author, 'blog-detail__avatar')}
                        <div class="blog-detail__author-info">
                            ${this.authorNameHtml(post.author, 'blog-detail__author-name')}
                            ${this.authorCultivationBadgeHtml(post.author, 'blog-author-cultivation--detail')}
                            <div class="blog-detail__author-meta">
                                <span>${escapeHtml(ROLE_LABELS[post.author?.role] || '')}</span>
                                <span>·</span>
                                <span>${escapeHtml(timeAgo(post.created_at))}</span>
                                <span>·</span>
                                <span>${post.view_count || 0} 次浏览</span>
                            </div>
                        </div>
                    </div>
                    ${actionButtons ? `<div class="blog-detail__actions">${actionButtons}</div>` : ''}
                </div>
                ${tags ? `<div class="blog-post-card__tags blog-detail__tags">${tags}</div>` : ''}
                ${editorialCover}
                <div class="blog-detail__body">${renderMarkdownHtml(post.content_md || '')}</div>
                <div class="blog-detail__interactions">
                    <button type="button" class="blog-interact-btn${post.is_liked ? ' is-active--like' : ''}" data-like-post="${post.id}">
                        ${post.is_liked ? SVG.heartFill : SVG.heart}
                        <span class="blog-interact-btn__label">点赞</span>
                        <span class="blog-interact-btn__count" data-blog-like-count="${post.id}">${formatCompactNumber(post.like_count)}</span>
                    </button>
                    <button type="button" class="blog-interact-btn${post.is_bookmarked ? ' is-active--bookmark' : ''}" data-bookmark-post="${post.id}">
                        ${post.is_bookmarked ? SVG.bookmarkFill : SVG.bookmark}
                        <span class="blog-interact-btn__label">收藏</span>
                        <span class="blog-interact-btn__count" data-blog-bookmark-count="${post.id}">${formatCompactNumber(post.bookmark_count)}</span>
                    </button>
                </div>
                ${this.commentSectionHtml(post)}
            </article>
        `;
    }

    commentSectionHtml(post) {
        if (!post.allow_comments) {
            return '<section class="blog-comments"><div class="blog-empty"><div class="blog-empty__title">作者已关闭评论</div></div></section>';
        }

        return `
            <section class="blog-comments" data-blog-comments-section>
                <div class="blog-comments__header">
                    <h3 class="blog-comments__title">评论 ${post.comment_count || 0}</h3>
                </div>
                <div class="blog-comment-list">
                    ${(post._comments || []).length ? (post._comments || []).map((comment) => this.commentHtml(comment)).join('') : '<div class="blog-empty blog-empty--compact"><div class="blog-empty__title">还没有评论，来抢沙发</div></div>'}
                </div>
                <div class="blog-comment-composer" data-blog-comment-composer>
                    <img class="blog-comment-composer__avatar" src="${escapeHtml(this.currentAvatarUrl)}" alt="${escapeHtml(this.userName)}">
                    <div class="blog-comment-composer__panel">
                        <div class="blog-comment-replying" data-blog-replying hidden></div>
                        <textarea class="blog-comment-composer__input" data-blog-comment-input rows="3" placeholder="写下你的观点、代码片段或补充说明。输入 @管家 可以邀请 AI 管家参与讨论..."></textarea>
                        <div class="blog-comment-media-strip" data-blog-comment-custom-emoji-preview hidden></div>
                        <div class="blog-comment-media-strip" data-blog-comment-attachment-preview hidden></div>
                        <div class="blog-comment-toolbar" data-blog-comment-toolbar>
                            <div class="blog-comment-toolbar__group">
                                <button type="button" class="blog-toolbar-chip blog-toolbar-chip--format" data-blog-comment-format="quote">引用</button>
                                <button type="button" class="blog-toolbar-chip blog-toolbar-chip--format" data-blog-comment-format="code">代码</button>
                                <button type="button" class="blog-toolbar-chip blog-toolbar-chip--format" data-blog-comment-format="codeblock">代码块</button>
                                <button type="button" class="blog-toolbar-chip blog-toolbar-chip--ai" data-blog-comment-format="mention-housekeeper">@管家</button>
                                <button type="button" class="blog-toolbar-chip" data-blog-comment-emoji-toggle>${SVG.smile}<span>表情</span></button>
                                <button type="button" class="blog-toolbar-chip" data-blog-comment-custom-emoji-toggle>${SVG.image}<span>自定义表情</span></button>
                                <button type="button" class="blog-toolbar-chip" data-blog-comment-upload>${SVG.plus}<span>图片</span></button>
                                <input type="file" accept="image/png,image/jpeg,image/gif,image/webp" data-blog-comment-file-input hidden multiple>
                            </div>
                            <div class="blog-comment-toolbar__hint">支持 Markdown、图片和表情；@管家 可邀请 AI 回复。</div>
                            <div class="blog-comment-toolbar__panels">
                                <div class="blog-comment-emoji-anchor" data-blog-comment-emoji-anchor></div>
                                <div class="blog-comment-custom-emoji-panel" data-blog-comment-custom-emoji-panel hidden></div>
                            </div>
                        </div>
                        <div class="blog-comment-composer__actions">
                            <button type="button" class="btn btn-ghost btn-sm" data-blog-reply-cancel hidden>取消回复</button>
                            <button type="button" class="btn btn-primary btn-sm" data-blog-submit-comment>发送评论</button>
                        </div>
                    </div>
                </div>
            </section>
        `;
    }

    commentHtml(comment) {
        const actions = [
            `<button type="button" class="blog-comment-action${comment.is_liked ? ' blog-comment-action--liked' : ''}" data-like-comment="${comment.id}">
                ${comment.is_liked ? SVG.heartFill : SVG.heart}
                <span class="blog-comment-action__count">${comment.like_count || 0}</span>
            </button>`,
            comment.can_reply ? `<button type="button" class="blog-comment-action" data-reply-to="${comment.id}" data-reply-name="${escapeHtml(comment.author?.display_name || '')}">回复</button>` : '',
            comment.can_delete ? `<button type="button" class="blog-comment-action blog-comment-action--delete" data-delete-comment="${comment.id}">删除</button>` : '',
        ].filter(Boolean).join('');

        return `
            <article class="blog-comment">
                ${this.authorAvatarHtml(comment.author, 'blog-comment__avatar')}
                <div class="blog-comment__body">
                    <div class="blog-comment__author">
                        ${this.authorNameHtml(comment.author, 'blog-comment__author-name')}
                        ${this.authorCultivationBadgeHtml(comment.author, 'blog-author-cultivation--comment')}
                        <span class="blog-comment__author-role">${escapeHtml(ROLE_LABELS[comment.author?.role] || '')}</span>
                        <span class="blog-comment__time">${escapeHtml(timeAgo(comment.created_at))}</span>
                    </div>
                    ${comment.content_md ? `<div class="blog-comment__content">${renderMarkdownHtml(comment.content_md)}</div>` : ''}
                    ${this.commentCustomEmojiHtml(comment.custom_emojis)}
                    ${this.commentAttachmentsHtml(comment.attachments)}
                    <div class="blog-comment__actions">${actions}</div>
                    ${(comment.replies || []).length ? `<div class="blog-comment__replies">${comment.replies.map((reply) => this.commentHtml(reply)).join('')}</div>` : ''}
                </div>
            </article>
        `;
    }

    commentCustomEmojiHtml(items = []) {
        if (!items?.length) return '';
        return `
            <div class="blog-rich-emojis">
                ${items.map((item) => `
                    <div class="blog-rich-emoji">
                        <img src="${escapeHtml(item.image_url || '')}" alt="${escapeHtml(item.name || '自定义表情')}" loading="lazy" decoding="async">
                    </div>
                `).join('')}
            </div>
        `;
    }

    commentAttachmentsHtml(items = []) {
        if (!items?.length) return '';
        return `
            <div class="blog-rich-attachments">
                ${items.map((item, index) => `
                    <a class="blog-rich-attachment" href="${escapeHtml(item.url || '#')}" target="_blank" rel="noreferrer noopener">
                        <img src="${escapeHtml(item.url || '')}" alt="${escapeHtml(item.name || `图片 ${index + 1}`)}" loading="lazy" decoding="async">
                        <span>${escapeHtml(item.name || `图片 ${index + 1}`)}</span>
                    </a>
                `).join('')}
            </div>
        `;
    }

    skeletonHtml(count) {
        return Array.from({ length: count }, () => '<div class="blog-skeleton" style="height: 140px; margin-bottom: var(--spacing-md);"></div>').join('');
    }

    emptyHtml(title) {
        return `
            <div class="blog-empty">
                <svg class="blog-empty__icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1 0-5H20"/></svg>
                <div class="blog-empty__title">${escapeHtml(title || '暂无内容')}</div>
                <div class="blog-empty__desc">点击右上角“写帖子”开始发布内容</div>
            </div>
        `;
    }
}

document.addEventListener('DOMContentLoaded', () => {
    const shell = $('[data-blog-center]');
    if (!shell) return;
    const app = new BlogCenter(shell);
    app.init();
});
