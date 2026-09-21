# Documentation images

These are screenshots of the real interface and its PNG export, populated only
with fictional projects and handwritten example prompts. No personal history,
user files, or session directories are read by the generator.

To regenerate from the repository root, with Playwright and Chrome available:

```sh
node scripts/readme_screenshots.cjs
```

The script starts an isolated loopback fixture server, blocks outbound requests,
and captures a fixed viewport, timezone, and light theme. It closes its own server
and browser when finished. Playwright is a development dependency only; the
application still has no third-party runtime dependencies.
