// Copy buttons, and replaying the proof. The page is complete without this file.
(() => {
  for (const button of document.querySelectorAll('[data-copy]')) {
    const label = button.textContent;
    button.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(button.dataset.copy);
        button.textContent = 'Copied';
      } catch {
        button.textContent = 'Select and copy';
      }
      button.dataset.done = '';
      setTimeout(() => { button.textContent = label; delete button.dataset.done; }, 1600);
    });
  }

  const proof = document.querySelector('.proof');
  const replay = proof && proof.querySelector('.replay');
  if (!replay || !matchMedia('(prefers-reduced-motion: no-preference)').matches) return;

  // Stacked layouts: the proof is below the fold at load, so play it when it comes into view.
  const seen = new IntersectionObserver((entries) => {
    if (entries.some((e) => e.isIntersecting)) { proof.classList.add('play'); seen.disconnect(); }
  }, { rootMargin: '0px 0px -12% 0px' });
  seen.observe(proof);

  // CSS animations restart when their elements are replaced, so replay swaps the sheets for fresh copies.
  replay.hidden = false;
  replay.addEventListener('click', () => {
    for (const sheet of proof.querySelectorAll('.sheet')) sheet.replaceWith(sheet.cloneNode(true));
  });
})();
