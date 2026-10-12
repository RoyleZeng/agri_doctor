Legacy book.tndais.gov.tw did not send a complete TLS chain on 2026-09-08.
twca_intermediates.pem contains two intermediate (including cross-signed) certificates:

- https://sslserver.twca.com.tw/cacert/Cyber_SSL_2023.crt
- https://sslserver.twca.com.tw/cacert/cyber_root_2022.crt

Both were downloaded over verified HTTPS from TWCA. The cross-signed certificate
and the SSL intermediate were verified with openssl against certifi 2026.07.22's
existing TWCA Global Root CA. No new self-signed root was trusted. No verify=False.
This bundle is used only by the planning reference crawler, not global settings.
See https://www.twca.com.tw/news_detail/4e8c6c2d-7d7f-4257-a709-16ee41f3a155
for the authority's chain transition notice.
