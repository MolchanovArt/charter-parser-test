#!/usr/bin/env bash
set -euo pipefail

mkdir -p data/raw
curl -L "https://shippingforum.wordpress.com/wp-content/uploads/2012/09/voyage-charter-example.pdf" -o data/raw/voyage-charter-example.pdf
printf "Downloaded to data/raw/voyage-charter-example.pdf
"
