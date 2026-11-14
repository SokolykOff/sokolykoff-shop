/* Плавающий AI-консультант SokolykOff Shop. Весь пользовательский текст вставляется через
   textContent (никакого innerHTML с пользовательскими данными -> нет XSS). */
(function () {
  'use strict';
  var QUICK = ['Игровой ПК до 1500$', 'Тихая клавиатура до 200$', 'Ноутбук до 700$', 'Ускоритель для нейросетей'];
  var modal, body, input, sendBtn, fab, busy = false;

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }
  function scrollDown() { body.scrollTop = body.scrollHeight; }
  function addMsg(text, who) {
    var m = el('div', 'sk-ai-msg sk-ai-msg--' + who, text);
    body.appendChild(m); scrollDown(); return m;
  }
  function addItems(host, items) {
    if (!items || !items.length) return;
    var list = el('div', 'sk-ai-items');
    items.forEach(function (it) {
      var a = el('a', 'sk-ai-item'); a.href = it.url;
      var img = el('img'); img.alt = it.title; img.loading = 'lazy';
      img.src = it.image || '/static/shop/placeholder-product.svg';
      var box = el('div'); box.appendChild(el('b', '', it.title)); box.appendChild(el('span', '', '$' + it.price));
      a.appendChild(img); a.appendChild(box); list.appendChild(a);
    });
    host.appendChild(list);
  }
  function toggle(force) {
    var open = typeof force === 'boolean' ? force : !modal.classList.contains('is-open');
    modal.classList.toggle('is-open', open);
    fab.setAttribute('aria-expanded', String(open));
    if (open) { setTimeout(function () { input.focus(); }, 50); }
  }
  function send(text) {
    text = (text || input.value).trim();
    if (!text || busy) return;
    busy = true; sendBtn.disabled = true; input.value = '';
    addMsg(text, 'user');
    var typing = el('div', 'sk-ai-msg sk-ai-msg--bot sk-ai-typing');
    typing.innerHTML = '<span></span><span></span><span></span>'; // статичная разметка, без данных
    body.appendChild(typing); scrollDown();
    fetch('/api/ai-assistant/', {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': decodeURIComponent((document.cookie.split('; ').find(function (c) { return c.startsWith('csrftoken='); }) || '=').split('=')[1]) }, body: JSON.stringify({ message: text })
    }).then(function (r) { if (!r.ok) throw new Error('http ' + r.status); return r.json(); })
      .then(function (data) {
        typing.remove();
        var m = addMsg(data.reply || 'Не удалось подобрать товары.', 'bot');
        addItems(m, data.items); scrollDown();
      })
      .catch(function () { typing.remove(); addMsg('Сервис подбора временно недоступен. Попробуйте ещё раз через минуту.', 'bot'); })
      .then(function () { busy = false; sendBtn.disabled = false; input.focus(); });
  }
  function init() {
    fab = document.getElementById('skAiFab'); modal = document.getElementById('skAiModal');
    if (!fab || !modal) return;
    body = document.getElementById('skAiBody'); input = document.getElementById('skAiInput');
    sendBtn = document.getElementById('skAiSend');
    var chips = document.getElementById('skAiChips');
    QUICK.forEach(function (q) {
      var b = el('button', 'sk-ai-chip', q); b.type = 'button';
      b.addEventListener('click', function () { send(q); }); chips.appendChild(b);
    });
    fab.addEventListener('click', function () { toggle(); });
    document.getElementById('skAiClose').addEventListener('click', function () { toggle(false); });
    sendBtn.addEventListener('click', function () { send(); });
    input.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); send(); } });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') toggle(false); });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
