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
    get(url) {
        return this.request(url);
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
        this.currentAvatarUrl = '/api/profile/avatar';
        this.composeUserMap = new Map();
        this.commentDraft = this.createEmptyCommentDraft();
        this.searchTimer = null;
        this.userSearchTimer = null;

        this.state = {
            currentView: 'feed',
            currentNav: 'feed',
            currentSort: 'latest',
            detailPostId: null,
            detailPost: null,
            posts: [],
            myPosts: [],
            bookmarkPosts: [],
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
            composeClassesLoaded: false,
            customEmojiLibrary: [],
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

    init() {
        this.bindEvents();
        this.ensureCustomEmojiLibrary();

        const url = new URL(window.location.href);
        const postId = Number(url.searchParams.get('post') || 0);
        if (postId) {
            this.showDetail(postId);
            return;
        }
        this.loadFeed();
    }

    bindEvents() {
        this.shell.addEventListener('click', (event) => this.handleClick(event));
        this.shell.addEventListener('change', (event) => this.handleChange(event));
        this.shell.addEventListener('input', (event) => this.handleInput(event));
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
            this.updateSortTabs();
            this.loadFeed();
            return;
        }

        const navButton = event.target.closest('[data-blog-nav]');
        if (navButton) {
            this.setNav(navButton.dataset.blogNav || 'feed');
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

        const tagButton = event.target.closest('[data-blog-tag]');
        if (tagButton) {
            const tag = tagButton.dataset.blogTag || '';
            const searchInput = $('[data-blog-search]', this.shell);
            if (searchInput) searchInput.value = tag;
            this.state.page = 1;
            this.loadFeed();
            return;
        }

        const postCard = event.target.closest('[data-blog-post-id]');
        if (postCard && !event.target.closest('button, a, input, textarea, select')) {
            this.showDetail(Number(postCard.dataset.blogPostId));
        }
    }

    handleChange(event) {
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
            return;
        }
        if (event.target.matches('[data-blog-compose-author-mode]')) {
            this.updateAuthorModeHint(event.target.value);
        }
    }

    handleInput(event) {
        if (event.target.matches('[data-blog-search]')) {
            window.clearTimeout(this.searchTimer);
            this.searchTimer = window.setTimeout(() => {
                this.state.page = 1;
                this.loadFeed();
            }, 320);
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
            this.showCurrentListView();
            this.refreshCurrentList();
            window.history.replaceState({}, '', '/blog');
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
            button.classList.toggle('is-active', button.dataset.blogSort === this.state.currentSort);
        });
    }

    updateNavTabs() {
        $$('[data-blog-nav]', this.shell).forEach((button) => {
            button.classList.toggle('is-active', button.dataset.blogNav === this.state.currentNav);
        });
    }

    setNav(nav) {
        this.state.currentNav = nav;
        this.updateNavTabs();
        if (nav === 'feed') {
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

    setMyPostsFilter(filterValue) {
        this.state.myPostsFilter = filterValue === 'all' ? null : filterValue;
        $$('[data-blog-myposts-filter]', this.shell).forEach((button) => {
            button.classList.toggle('is-active', button.dataset.blogMypostsFilter === filterValue);
        });
        this.state.myPage = 1;
        this.loadMyPosts();
    }

    showView(viewName) {
        $$('[data-blog-view]', this.shell).forEach((view) => {
            view.hidden = view.dataset.blogView !== viewName;
        });
        this.state.currentView = viewName;
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

    filterByAuthor(identity, name = '') {
        const normalizedIdentity = String(identity || '').trim();
        if (!normalizedIdentity) return;
        this.state.authorFilter = {
            identity: normalizedIdentity,
            name: String(name || '').trim() || '该用户',
        };
        this.state.currentNav = 'feed';
        this.state.currentView = 'feed';
        this.state.page = 1;
        const searchInput = $('[data-blog-search]', this.shell);
        if (searchInput) searchInput.value = '';
        this.updateNavTabs();
        this.showView('feed');
        this.updateAuthorFilterBanner();
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
        popover.innerHTML = `
            <div class="blog-user-popover__name">${escapeHtml(name)}</div>
            <div class="blog-user-popover__actions">
                <button type="button" class="blog-user-popover__btn" data-blog-author-posts="${escapeHtml(identity)}" data-author-name="${escapeHtml(name)}">ta的帖子</button>
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

    async loadFeed({ append = false } = {}) {
        const container = $('[data-blog-feed]', this.shell);
        if (!container) return;
        if (!append) {
            container.innerHTML = this.skeletonHtml(3);
        }
        const search = $('[data-blog-search]', this.shell)?.value?.trim() || '';
        const url = new URL('/api/blog/posts', window.location.origin);
        url.searchParams.set('sort', this.state.currentSort);
        url.searchParams.set('page', String(this.state.page));
        url.searchParams.set('limit', String(POSTS_PAGE_SIZE));
        if (search) url.searchParams.set('tag', search);
        if (this.state.authorFilter?.identity) {
            url.searchParams.set('author', this.state.authorFilter.identity);
        }
        this.updateAuthorFilterBanner();

        try {
            const data = await api.get(`${url.pathname}${url.search}`);
            const nextPosts = data.posts || [];
            this.state.hasMore = Boolean(data.has_more);
            this.state.posts = append ? [...this.state.posts, ...nextPosts] : nextPosts;
            if (append) {
                container.insertAdjacentHTML('beforeend', nextPosts.map((post) => this.postCardHtml(post)).join(''));
            } else {
                container.innerHTML = this.state.posts.length
                    ? this.state.posts.map((post) => this.postCardHtml(post)).join('')
                    : this.emptyHtml('还没有可浏览的帖子');
            }
            if (append && !nextPosts.length && this.state.page > 1) this.state.page -= 1;
        } catch (error) {
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

    async showDetail(postId) {
        const container = $('[data-blog-detail-content]', this.shell);
        if (!container || !postId) return;
        this.showView('detail');
        this.state.detailPostId = postId;
        container.innerHTML = this.skeletonHtml(1);

        try {
            const data = await api.get(`/api/blog/posts/${postId}`);
            const post = data.post;
            this.state.detailPost = post;
            this.closeCommentPanels();
            this.resetCommentDraft();
            container.innerHTML = this.detailHtml(post);
            this.renderCommentDraftState();
            this.initCommentComposer();
            window.history.replaceState({}, '', `/blog?post=${postId}`);
        } catch (error) {
            this.state.detailPost = null;
            container.innerHTML = this.emptyHtml(error.message || '帖子详情加载失败');
        }
    }

    openComposer(post = null) {
        const modal = $('[data-blog-composer-modal]', this.shell);
        if (!modal) return;

        $('[data-blog-compose-title]', this.shell).value = post?.title || '';
        $('[data-blog-compose-content]', this.shell).value = post?.content_md || '';
        $('[data-blog-compose-tags]', this.shell).value = (post?.custom_tags || post?.tags || []).join(', ');
        $('[data-blog-compose-comments]', this.shell).checked = post?.allow_comments ?? true;
        this.setSelectedAuthorMode(post?.author_display_mode || post?.author?.display_mode || 'real_name');

        const visibility = post?.visibility || 'public';
        const visibilitySelect = $('[data-blog-compose-visibility]', this.shell);
        if (visibilitySelect) visibilitySelect.value = visibility;

        this.state.editingPostId = post?.id || null;
        this.state.uploadedImages = uniqueMediaItems(post?.attachments || []);
        this.state.selectedUsers = (post?.visible_user_identities || []).map((identity) => {
            const normalizedIdentity = String(identity || '');
            const cached = this.composeUserMap.get(normalizedIdentity);
            return cached || { identity: normalizedIdentity, name: normalizedIdentity };
        });

        $('[data-blog-composer-title]', this.shell).textContent = post ? '编辑帖子' : '写帖子';
        this.renderImagePreviews();
        this.renderSelectedUsers();
        this.updateVisibilityOptions(visibility, post?.visible_class_id || null);
        this.updateAuthorModeHint();

        modal.hidden = false;
    }

    closeComposer() {
        const modal = $('[data-blog-composer-modal]', this.shell);
        if (modal) modal.hidden = true;
        this.state.editingPostId = null;
        this.state.uploadedImages = [];
        this.state.selectedUsers = [];
        this.setSelectedAuthorMode('real_name');
        this.updateAuthorModeHint();
        this.renderImagePreviews();
        this.renderSelectedUsers();
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
                ? `将以昵称“${this.userNickname}”发帖，并自动附带班级标签。`
                : '当前还没有设置昵称，无法使用昵称发帖。';
            return;
        }
        if (mode === 'anonymous') {
            hint.textContent = '将以匿名身份发帖，帖子不会自动附带班级标签，头像也会隐藏为默认样式。';
            return;
        }
        hint.textContent = '默认使用真实名字发布；使用真实名字或昵称时会自动带上班级标签。';
    }

    async savePost(status) {
        const title = $('[data-blog-compose-title]', this.shell)?.value?.trim() || '';
        const content = $('[data-blog-compose-content]', this.shell)?.value?.trim() || '';
        const visibility = $('[data-blog-compose-visibility]', this.shell)?.value || 'public';
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
            if (this.state.editingPostId) {
                await api.put(`/api/blog/posts/${this.state.editingPostId}`, payload);
                showToast('帖子已更新', 'success');
            } else {
                await api.post('/api/blog/posts', payload);
                showToast(status === 'draft' ? '草稿已保存' : '帖子已发布', 'success');
            }

            this.closeComposer();
            if (this.state.currentView === 'detail' && this.state.detailPostId) {
                await this.showDetail(this.state.detailPostId);
            } else {
                this.refreshCurrentList();
            }
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
            const button = $(selector, this.shell);
            if (button) {
                const isLiked = Boolean(data.liked);
                button.classList.toggle('is-active--like', isLiked);
                button.classList.toggle('blog-comment-action--liked', isLiked);
                const icon = $('svg', button);
                if (icon) {
                    icon.outerHTML = isLiked ? SVG.heartFill : SVG.heart;
                }
                const count = $('.blog-interact-btn__count, .blog-comment-action__count', button);
                if (count) count.textContent = String(data.like_count ?? 0);
            }
        } catch (error) {
            showToast(error.message || '点赞失败', 'error');
        }
    }

    async toggleBookmark(postId) {
        try {
            const data = await api.post(`/api/blog/posts/${postId}/bookmark`, {});
            const button = $(`[data-bookmark-post="${postId}"]`, this.shell);
            if (button) {
                const bookmarked = Boolean(data.bookmarked);
                button.classList.toggle('is-active--bookmark', bookmarked);
                const icon = $('svg', button);
                if (icon) {
                    icon.outerHTML = bookmarked ? SVG.bookmarkFill : SVG.bookmark;
                }
                const count = $('.blog-interact-btn__count', button);
                if (count) count.textContent = String(data.bookmark_count ?? 0);
            }
        } catch (error) {
            showToast(error.message || '收藏失败', 'error');
        }
    }

    async togglePin(postId) {
        try {
            const data = await api.post(`/api/blog/posts/${postId}/pin`, {});
            showToast(data.is_pinned ? '已置顶' : '已取消置顶', 'success');
            await this.refreshAfterDetailMutation(postId);
        } catch (error) {
            showToast(error.message || '置顶操作失败', 'error');
        }
    }

    async toggleFeature(postId) {
        try {
            const data = await api.post(`/api/blog/posts/${postId}/feature`, {});
            showToast(data.is_featured ? '已设为精华' : '已取消精华', 'success');
            await this.refreshAfterDetailMutation(postId);
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
            window.history.replaceState({}, '', '/blog');
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

    postCardHtml(post, { ownView = false } = {}) {
        const badges = [];
        if (post.is_pinned) badges.push('<span class="blog-badge blog-badge--pin">置顶</span>');
        if (post.is_featured) badges.push('<span class="blog-badge blog-badge--feature">精华</span>');
        if (post.status === 'draft') badges.push('<span class="blog-badge blog-badge--draft">草稿</span>');
        if (post.status === 'moderated') badges.push('<span class="blog-badge blog-badge--moderated">私密</span>');
        if (post.visibility !== 'public') badges.push(`<span class="blog-badge blog-badge--visibility">${escapeHtml(post.visibility_label || '权限可见')}</span>`);

        const tags = (post.tags || []).map((tag) => (
            `<button type="button" class="blog-tag" data-blog-tag="${escapeHtml(tag)}">${escapeHtml(tag)}</button>`
        )).join('');

        return `
            <article class="blog-post-card${post.is_pinned ? ' is-pinned' : ''}${post.is_featured ? ' is-featured' : ''}" data-blog-post-id="${post.id}">
                ${badges.length ? `<div class="blog-post-card__badges">${badges.join('')}</div>` : ''}
                <h3 class="blog-post-card__title">${escapeHtml(post.title || '')}</h3>
                <p class="blog-post-card__summary">${escapeHtml(post.summary || '')}</p>
                ${post.cover_image_hash ? `<img class="blog-post-card__cover" src="/api/blog/image/${escapeHtml(post.cover_image_hash)}" alt="" loading="lazy" decoding="async">` : ''}
                <div class="blog-post-card__meta">
                    <div class="blog-post-card__author">
                        ${this.authorAvatarHtml(post.author, 'blog-post-card__avatar')}
                        ${this.authorNameHtml(post.author, 'blog-post-card__author-name')}
                        <span class="blog-post-card__author-role">${escapeHtml(ROLE_LABELS[post.author?.role] || '')}</span>
                        <span class="blog-post-card__time">${escapeHtml(timeAgo(post.created_at))}</span>
                    </div>
                    <div class="blog-post-card__stats">
                        <span class="blog-stat">${SVG.eye}<span>${post.view_count || 0}</span></span>
                        <span class="blog-stat">${SVG.heart}<span>${post.like_count || 0}</span></span>
                        <span class="blog-stat">${SVG.comment}<span>${post.comment_count || 0}</span></span>
                    </div>
                </div>
                ${tags ? `<div class="blog-post-card__tags">${tags}</div>` : ''}
                ${ownView ? `<div class="blog-post-card__footnote">状态：${escapeHtml(this.statusLabel(post.status))}</div>` : ''}
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
        ].filter(Boolean).join('');

        const tags = (post.tags || []).map((tag) => (
            `<button type="button" class="blog-tag" data-blog-tag="${escapeHtml(tag)}">${escapeHtml(tag)}</button>`
        )).join('');

        return `
            <article class="blog-detail">
                ${metaBadges.length ? `<div class="blog-detail__badges">${metaBadges.join('')}</div>` : ''}
                <h1 class="blog-detail__title">${escapeHtml(post.title || '')}</h1>
                <div class="blog-detail__author-row">
                    <div class="blog-detail__author">
                        ${this.authorAvatarHtml(post.author, 'blog-detail__avatar')}
                        <div class="blog-detail__author-info">
                            ${this.authorNameHtml(post.author, 'blog-detail__author-name')}
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
                <div class="blog-detail__body">${renderMarkdownHtml(post.content_md || '')}</div>
                <div class="blog-detail__interactions">
                    <button type="button" class="blog-interact-btn${post.is_liked ? ' is-active--like' : ''}" data-like-post="${post.id}">
                        ${post.is_liked ? SVG.heartFill : SVG.heart}
                        <span class="blog-interact-btn__label">点赞</span>
                        <span class="blog-interact-btn__count">${post.like_count || 0}</span>
                    </button>
                    <button type="button" class="blog-interact-btn${post.is_bookmarked ? ' is-active--bookmark' : ''}" data-bookmark-post="${post.id}">
                        ${post.is_bookmarked ? SVG.bookmarkFill : SVG.bookmark}
                        <span class="blog-interact-btn__label">收藏</span>
                        <span class="blog-interact-btn__count">${post.bookmark_count || 0}</span>
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
                            <div class="blog-comment-toolbar__hint">支持 Markdown 多行评论、图片、自定义表情；@管家 后会由 AI 结合上下文回复。</div>
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
