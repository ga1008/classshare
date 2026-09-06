/* Shared transport and bounded polling for career and resume workspaces. */
(function () {
  'use strict';
  var reads = new Map();
  async function request(url, options) {
    options = options || {};
    var method = options.method || 'GET';
    if (method === 'GET' && reads.has(url)) return reads.get(url);
    var promise = (async function () {
      var controller = new AbortController();
      var timer = setTimeout(function () { controller.abort(); }, options.timeout || 30000);
      var headers = Object.assign({}, options.headers || {}), body = options.body;
      if (body !== undefined && !(body instanceof FormData) && typeof body !== 'string') {
        headers['Content-Type'] = 'application/json'; body = JSON.stringify(body);
      }
      try {
        var response = await fetch(url, { method: method, body: body, headers: headers,
          credentials: 'same-origin', signal: controller.signal, keepalive: !!options.keepalive });
        var data = await response.json().catch(function () { return null; });
        if (!response.ok) {
          var detail = data && (data.detail || data.error || data.message);
          var error = new Error(typeof detail === 'string' ? detail : (detail && detail.message) ||
            (response.status === 401 ? '登录已失效，请重新登录后继续。' : '请求失败，请稍后重试。'));
          error.status = response.status; error.detail = detail || {}; error.data = data;
          var retryAfter = response.headers.get('Retry-After');
          if (retryAfter) error.retryAfterMs = /^\d+$/.test(retryAfter) ? Number(retryAfter) * 1000 : Math.max(0, Date.parse(retryAfter) - Date.now());
          throw error;
        }
        return data;
      } catch (error) {
        if (error.name === 'AbortError') error.message = '连接超时，你的内容仍保留在当前页面，可重试。';
        throw error;
      } finally { clearTimeout(timer); }
    })();
    if (method === 'GET') reads.set(url, promise);
    try { return await promise; } finally { if (reads.get(url) === promise) reads.delete(url); }
  }
  function poll(options) {
    var timer = null, stopped = false, busy = false, failures = 0;
    function schedule(delay) {
      clearTimeout(timer);
      if (!stopped) timer = setTimeout(tick, Math.max(1000, delay || options.interval || 6000) * (1 + Math.random() * 0.2));
    }
    async function run() {
      var result = await options.load(); failures = 0;
      if (options.onData) options.onData(result);
      if (options.done && options.done(result)) { stop(); return; }
      schedule(options.delay ? options.delay(result) : options.interval);
    }
    async function tick() {
      if (stopped || busy) return;
      if (document.hidden || navigator.onLine === false) { schedule(30000); return; }
      busy = true;
      try {
        // Serialize status reads across this origin's tabs, never AI execution.
        if (navigator.locks && navigator.locks.request) {
          await navigator.locks.request('career-tools-status-read', { ifAvailable: true }, async function (lock) {
            if (lock) await run(); else schedule(options.interval);
          });
        } else await run();
      } catch (error) {
        failures++;
        if (options.onError) options.onError(error, failures);
        if (error.status === 401 || error.status === 403 || error.status === 404) stop();
        else schedule(Math.max(Math.min(300000, error.retryAfterMs || 0), Math.min(60000, (options.interval || 6000) * Math.pow(2, Math.min(failures, 4)))));
      } finally { busy = false; }
    }
    function resume() { if (!stopped && !document.hidden && navigator.onLine !== false) { clearTimeout(timer); tick(); } }
    function stop() {
      stopped = true; clearTimeout(timer);
      document.removeEventListener('visibilitychange', resume); window.removeEventListener('online', resume);
      window.removeEventListener('pagehide', stop);
    }
    document.addEventListener('visibilitychange', resume); window.addEventListener('online', resume);
    window.addEventListener('pagehide', stop);
    if (options.immediate === false) schedule(); else tick();
    return { stop: stop, refresh: resume, active: function () { return !stopped; } };
  }
  var labels = { pending: '等待处理', queued: '等待处理', running: '处理中', retrying: '正在重试',
    succeeded: '已完成', done: '已完成', ready: '已完成', failed: '处理失败', cancelled: '已取消',
    canceled: '已取消', paused: '服务暂未开启', retry_wait: '等待稍后重试', result_ready: '正在保存结果', dead_letter: '处理失败，可重试', review_required: '需要处理', superseded: '资料已更新，任务已结束', not_requested: '可按需生成', stale: '资料已变化', review_ready: '待你确认', parsing: '正在解析', rendering: '正在生成文件', optimizing: '正在准备建议' };
  function pending(task) { return !!task && ['pending', 'queued', 'running', 'retrying', 'retry_wait', 'result_ready', 'generating', 'rendering', 'parsing', 'optimizing'].indexOf(task.status) >= 0; }
  window.CareerTools = { request: request, poll: poll, pending: pending, taskLabel: function (task) {
    return task.phase_label || labels[task.status] || '等待更新';
  } };
})();
