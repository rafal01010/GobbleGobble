<script lang="ts">
  import '../app.css';
  import type { LayoutProps } from './$types';

  let { children }: LayoutProps = $props();
</script>

<a class="skip-link" href="#main-content">Skip to content</a>

<div class="app-shell">
  <header class="shell-frame shell-header">
    <span class="wordmark">GobbleGobble</span>
  </header>

  <main class="shell-main" id="main-content" tabindex="-1">
    {@render children()}
  </main>
</div>
