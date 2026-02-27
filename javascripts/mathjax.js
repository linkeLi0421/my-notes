window.MathJax = {
  tex: {
    inlineMath: [['$', '$'], ['\\(', '\\)']],
    displayMath: [['$$', '$$'], ['\\[', '\\]']],
    processEscapes: true,
    processEnvironments: true
  },
  options: {
    skipHtmlTags: ['script', 'noscript', 'style', 'textarea', 'pre', 'code']
  },
  startup: {
    typeset: false,
    ready: () => {
      MathJax.startup.defaultReady();
      const typeset = () => MathJax.typesetPromise();
      if (window.document$) {
        document$.subscribe(typeset);
      } else {
        typeset();
      }
    }
  }
};
