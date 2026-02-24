window.MathJax = {
  tex: {
    inlineMath: [['$', '$'], ['\\(', '\\)']],
    displayMath: [['$$', '$$'], ['\\[', '\\]']],
    processEscapes: true,
    processEnvironments: true
  },
  options: {
    skipHtmlTags: ['script', 'noscript', 'style', 'textarea', 'pre', 'code']
  }
};

if (window.document$) {
  document$.subscribe(() => {
    MathJax.typesetPromise();
  });
} else {
  window.addEventListener('load', () => {
    MathJax.typesetPromise();
  });
}
