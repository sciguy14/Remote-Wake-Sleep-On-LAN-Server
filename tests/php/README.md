# PHP tests

Plain PHP (no PHPUnit). Needs the openssl extension. From the repository root run `php tests/php/run.php`;
it prints PASS/FAIL per test and exits non-zero on any failure.

Without a local PHP: `docker run --rm -v "$PWD":/app -w /app php:8.2-cli php tests/php/run.php` (also try `php:7.4-cli`).
