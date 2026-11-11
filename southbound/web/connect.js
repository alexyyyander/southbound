/* Pairing secret stays in the fragment: never in HTTP paths, referrers or logs. */
(() => {
  window.addEventListener('hashchange', () => location.reload());
  const params = new URLSearchParams(location.hash.slice(1));
  const code = params.get('code') || '';
  const status = document.getElementById('status');
  const connect = document.getElementById('connect');
  const fallback = document.getElementById('fallback');
  if (!/^[A-Za-z0-9_-]{40,128}$/.test(code)) {
    status.textContent = '请使用电脑生成的完整接入链接或二维码。'; return;
  }
  const query = new URLSearchParams({server: location.origin, code}).toString();
  connect.href = `intent://connect?${query}#Intent;scheme=southbound;package=io.southbound.endpoint;end`;
  fallback.href = `southbound://connect?${query}`;
  fallback.hidden = false;
  async function check() {
    try {
      const response = await fetch('/api/terminal/setup-status', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({pairing_code:code}), cache:'no-store'});
      if (response.status === 401) {
        connect.classList.add('off'); fallback.hidden=true;
        status.textContent='接入链接已过期或已撤销，请在电脑生成新链接。已配对的手机仍可正常使用。'; return;
      }
      if (!response.ok) throw new Error();
      const data = await response.json();
      connect.classList.remove('off');
      if (!data.paired) status.textContent='等待手机连接。安装后点击上面的“连接并启用”。';
      else {
        connect.textContent='打开已连接的 App';
        const test=data.test;
        if (!test) status.textContent='手机已连接。请在 App 开启接收，然后点“测试锁屏任务”。';
        else if (test.status==='verified') status.textContent='✓ 通知已发布\n✓ 播报已完成\n手机回执确认成功。请确认你实际看见通知、听见播报。';
        else if (test.status==='failed' || test.status==='cancelled') status.textContent='测试未完成：'+(test.error || test.status)+'\n请在 App 检查网络、通知权限和语音引擎。';
        else {
          const remaining=Math.max(0, Math.ceil((test.scheduled_at || 0)-test.server_time));
          status.textContent=remaining ? `测试已安排，${remaining} 秒后开始。现在可以锁屏。` : '正在等待手机回执…\n'+(test.receipts.length ? '✓ 通知已发布，等待播报完成。' : '正在发送通知。');
        }
      }
    } catch (_) { status.textContent='暂时连不上电脑，正在重试。请检查 Wi-Fi。'; }
    setTimeout(check,2000);
  }
  check();
})();
