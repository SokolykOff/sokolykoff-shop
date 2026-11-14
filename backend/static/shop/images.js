/* Фоллбэк для битых картинок и снятие скелетона после загрузки (карточки рендерит Vue). */
(function () {
  'use strict';
  var PLACEHOLDER = '/static/shop/placeholder-product.svg';
  function done(img) { var h = img.closest('.Card-picture, .ProductCard-photo'); if (h) h.classList.add('sk-img-loaded'); }
  document.addEventListener('error', function (e) {
    var t = e.target;
    if (t && t.tagName === 'IMG' && !t.dataset.skFallback) { t.dataset.skFallback = '1'; t.src = PLACEHOLDER; }
  }, true);
  document.addEventListener('load', function (e) { if (e.target && e.target.tagName === 'IMG') done(e.target); }, true);
})();
