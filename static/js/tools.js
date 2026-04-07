function safeMarkedParse(content, fallback = '') {
    // 处理 null, undefined, 空字符串等情况
    if (content == null || content === '') {
        return fallback;
    }

    try {
        // 确保内容是字符串
        const contentStr = String(content).trim();

        // 如果内容为空，返回回退值
        if (contentStr === '') {
            return fallback;
        }

        // 使用 marked 解析
        return marked.parse(contentStr);
    } catch (error) {
        console.error('Markdown 解析错误:', error, '原始内容:', content);
        // 返回原始内容作为代码块，避免格式问题
        return `<pre><code>${escapeHtml(String(content))}</code></pre>`;
    }
}

// HTML 转义辅助函数
function escapeHtml(unsafe) {
    return unsafe
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

window.safeMarkedParse = safeMarkedParse;
window.escapeHtml = escapeHtml;
