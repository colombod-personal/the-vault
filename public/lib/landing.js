// The landing page's two small behaviours (#437, public/index.html): the Copy buttons next to the address, and "Watch the demo"
// starting the video. Plain script, no React: it works while the app's scripts are still loading. The video has preload="none",
// so no video bytes load until the visitor asks for it.
(function () {
  document.querySelectorAll('#landing .copy').forEach(function (b) {
    b.addEventListener('click', function () {
      var text = b.parentNode.querySelector('code').textContent, label = b.getAttribute('data-label') || 'Copy';
      if (!navigator.clipboard) return;
      navigator.clipboard.writeText(text).then(function () {
        b.textContent = 'Copied';
        setTimeout(function () { b.textContent = label; }, 1500);
      });
    });
  });
  var video = document.querySelector('#demo video');
  document.querySelectorAll('[data-play-demo]').forEach(function (a) {
    a.addEventListener('click', function (e) {
      if (!video) return;
      e.preventDefault();
      document.getElementById('demo').scrollIntoView({ behavior: 'smooth', block: 'center' });
      video.focus({ preventScroll: true });
      var p = video.play();
      if (p && p.catch) p.catch(function () {});
    });
  });
})();
