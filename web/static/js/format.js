/*
 * Общие функции форматирования витрины KZ Price Hunter (P04 U08 — первый шаг разделения index.html на модули).
 *
 * В браузере подключается до основного скрипта index.html и выставляет глобальные escapeHtml, fmtPrice, isStale,
 * freshnessBadge (существующий код вызывает их напрямую). В Node экспортирует те же функции для тестов
 * (test_frontend_format.cjs).
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) {
    module.exports = api;
  } else {
    root.KZFormat = api;
    Object.assign(root, api);
  }
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const ESCAPES = {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'};

  /** Экранирование для вставки в HTML: текст и значения атрибутов. */
  function escapeHtml(text) {
    return String(text ?? '').replace(/[&<>"']/g, ch => ESCAPES[ch]);
  }

  /** Цена в тенге с разделителем тысяч: 1234567 → «1 234 567 ₸»; нечисловое → «0 ₸». */
  function fmtPrice(val) {
    const amount = Number(val);
    if (!Number.isFinite(amount)) return '0 ₸';
    return amount.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ' ') + ' ₸';
  }

  /** Цена предложения устарела (P02: Stale — не участвует в лучшей цене и сравнении). */
  function isStale(it) {
    return !!it && it.freshness === 'stale';
  }

  /** Бейдж свежести цены (P02): Aging — «цена от <дата>», Stale — «цена устарела (N дн.)», иначе пусто. */
  function freshnessBadge(it) {
    if (!it || (it.freshness !== 'aging' && it.freshness !== 'stale')) return '';
    const seen = it.last_seen_at ? new Date(String(it.last_seen_at).replace(' ', 'T')) : null;
    const when = seen && !isNaN(seen) ? seen.toLocaleDateString('ru-RU', {day: 'numeric', month: 'short'}) : '';
    if (it.freshness === 'aging') {
      return `<span class="inline-block mt-0.5 px-1.5 py-0.5 rounded text-[10px] bg-amber-500/15 text-amber-300 border border-amber-500/30" title="Цена получена более суток назад">⏳ цена от ${escapeHtml(when)}</span>`;
    }
    const days = Math.max(1, Math.round((Number(it.age_hours) || 72) / 24));
    return `<span class="inline-block mt-0.5 px-1.5 py-0.5 rounded text-[10px] bg-rose-500/15 text-rose-300 border border-rose-500/30" title="Магазин давно не отдавал этот товар — цена может быть неактуальной">⚠️ цена устарела (${days} дн.)</span>`;
  }

  return {escapeHtml, fmtPrice, isStale, freshnessBadge};
});
