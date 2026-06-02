-- Update Yandex Cloud relay server IP after infrastructure change.
UPDATE vpn_servers
SET server_host = '51.250.95.247',
    panel_url = 'https://51.250.95.247:54023/Cq6xxAccNLaSEBcR0L'
WHERE label LIKE '%LTE%';
