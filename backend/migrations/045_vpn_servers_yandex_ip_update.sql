-- Update Yandex Cloud relay server IP after infrastructure change.
UPDATE vpn_servers
SET server_host = '46.21.247.80',
    panel_url = 'https://46.21.247.80:54023/Cq6xxAccNLaSEBcR0L'
WHERE server_host = '51.250.65.51' AND label LIKE '%LTE%';
