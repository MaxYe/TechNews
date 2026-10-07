(function() {
  const arts = document.querySelectorAll('article');
  const tweets = [];
  const seen = new Set();
  arts.forEach(a => {
    const link = a.querySelector('a[href*="/status/"]');
    if (!link) return;
    const m = link.getAttribute('href').match(/\/status\/(\d+)/);
    const id = m ? m[1] : null;
    if (!id || seen.has(id)) return;
    seen.add(id);
    const textEl = a.querySelector('[data-testid="tweetText"]');
    if (!textEl) return; // skip non-tweet articles (promos etc)
    const text = textEl.innerText;
    const timeEl = a.querySelector('time');
    const date = timeEl ? timeEl.getAttribute('datetime') : '';
    function metricFromTestid(tid) {
      const el = a.querySelector('[data-testid="' + tid + '"]');
      if (!el) return 0;
      const aria = el.getAttribute('aria-label') || '';
      const mm = aria.match(/([\d,\.]+)/);
      return mm ? parseInt(mm[1].replace(/[,\\.]/g,'')) : 0;
    }
    const replies = metricFromTestid('reply');
    const retweets = metricFromTestid('retweet');
    const likes = metricFromTestid('like');
    // views: from analytics link aria-label
    let views = 0;
    const viewsEl = a.querySelector('a[aria-label*="查看"]') || a.querySelector('span[aria-label*="查看"]');
    if (viewsEl) {
      const va = viewsEl.getAttribute('aria-label') || '';
      const vm = va.match(/([\d,\.]+)/);
      views = vm ? parseInt(vm[1].replace(/[,\\.]/g,'')) : 0;
    }
    // bookmarks from combined: look at any aria-label with 书签
    let bookmarks = 0;
    const bmEl = a.querySelector('[aria-label*="书签"]');
    if (bmEl) {
      const ba = bmEl.getAttribute('aria-label') || '';
      const parts = ba.split('、');
      parts.forEach(p => {
        if (p.indexOf('书签') >= 0) {
          const bm = p.match(/([\d,\.]+)/);
          if (bm) bookmarks = parseInt(bm[1].replace(/[,\\.]/g,''));
        }
      });
    }
    tweets.push({id: id, text: text, date: date, replies: replies, retweets: retweets, likes: likes, bookmarks: bookmarks, views: views, url: 'https://x.com' + link.getAttribute('href').split('?')[0]});
  });
  return JSON.stringify({count: tweets.length, tweets: tweets});
})()
