# Docker image for the web frontend: PHP with Apache
# - Base image: `php:8.4-apache` provides PHP 8.4 + Apache HTTPD.
#   Ensure the chosen tag supports your target architecture
#   (use `docker buildx` / multi-arch images on Raspberry Pi).
FROM php:8.4-apache

# Enable Apache mod_rewrite for pretty URLs and .htaccess rules
RUN a2enmod rewrite

# Install PHP extensions and APCu cache:
# - `pdo` + `pdo_mysql`: PDO MySQL driver for database access (used by `db.php`).
# - `apcu` (installed via PECL): userland shared memory cache used by metrics.
RUN docker-php-ext-install pdo pdo_mysql \
    && pecl install apcu \
    && docker-php-ext-enable apcu

# Ensure Apache's runtime user owns the webroot so PHP/Apache can write
# (uploads, temp files, and some demo scripts expect write access).
RUN chown -R www-data:www-data /var/www/html

# ── OPTIONAL: Apache worker-pool tuning (caps throughput, protects the Pi) ──
# Uncomment to apply server-pi/web/apache/nexus.conf. Read the trade-off
# notes in that file first — capping workers also caps the dashboard's
# "queue depth" (it can't exceed the number of busy workers).
# COPY apache/nexus.conf /etc/apache2/conf-available/nexus.conf
# RUN a2enconf nexus