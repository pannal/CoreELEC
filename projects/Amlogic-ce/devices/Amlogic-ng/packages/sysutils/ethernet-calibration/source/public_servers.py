# SPDX-License-Identifier: GPL-2.0-or-later
"""Dated factual snapshot of the complete iPerf.fr public iPerf3 table."""

SOURCE_URL = 'https://iperf.fr/iperf-servers.php'
SOURCE_DATE = '2026-11-22'
SOURCE_RETRIEVED_AT = '2026-11-22T00:36:11.0726024Z'
SOURCE_SHA256 = 'e358d78ccde56fb9f81c902c0f9c745f32c69926808da807b755f6934a326493'
IPV6_SOURCE = 'http://ping.scaleway.com/'

# Hosts and locations with matching lengths correspond in the source table.
ROWS = (
    ('Europe', ('ping.online.net', 'ping6.online.net', 'ping-90ms.online.net', 'ping6-90ms.online.net'),
     ('France / \u00cele-de-France',), 'Scaleway', 'online.net', 5200, 5209,
     ('TCP', 'UDP'), 'IPv4 or IPv6', 'OK 10/2026'),
    ('Europe', ('iperf3.moji.fr',), ('France / \u00cele-de-France',), 'Moji', 'Moji',
     5200, 5240, ('TCP', 'UDP'), 'IPv4 and IPv6', 'OK 10/2026'),
    ('Europe', ('speedtest.milkywan.fr',), ('France / \u00cele-de-France',), 'MilkyWan', 'MilkyWan',
     9200, 9240, ('TCP', 'UDP'), 'IPv4 and IPv6', 'OK 10/2026'),
    ('Europe', ('paris.bbr.iperf.bytel.fr', 'paris.cubic.iperf.bytel.fr',
                'mrs.bbr.iperf.bytel.fr', 'mrs.cubic.iperf.bytel.fr',
                'lyo.bbr.iperf.bytel.fr', 'lyo.cubic.iperf.bytel.fr',
                'tls.bbr.iperf.bytel.fr', 'tls.cubic.iperf.bytel.fr',
                'str.bbr.iperf.bytel.fr', 'str.cubic.iperf.bytel.fr',
                'poi.bbr.iperf.bytel.fr', 'poi.cubic.iperf.bytel.fr',
                'ren.bbr.iperf.bytel.fr', 'ren.cubic.iperf.bytel.fr'),
     ('France Paris BBR', 'France Paris Cubic', 'France Marseille BBR', 'France Marseille Cubic',
      'France Lyon BBR', 'France Lyon Cubic', 'France Toulouse BBR', 'France Toulouse Cubic',
      'France Strasbourg BBR', 'France Strasbourg Cubic', 'France Poitiers BBR', 'France Poitiers Cubic',
      'France Rennes BBR', 'France Rennes Cubic'),
     'Bouygues Telecom', 'Bouygues Telecom', 9200, 9240, ('TCP',), 'IPv4 and IPv6', 'OK 10/2026'),
    ('Europe', ('speedtest1.serverius.net', 'speedtest2.serverius.net', 'speedtest3.serverius.net'),
     ('Netherlands / Serverius DC1', 'Netherlands / Serverius DC2', 'Netherlands / Serverius DC3'),
     'Serverius', 'Serverius datacenters', 5002, 5002, ('TCP', 'UDP'), 'IPv4 and IPv6', 'OK 10/2026'),
    ('Europe', ('nl.iperf.014.fr',), ('Netherlands',), '014.fr', '014.fr',
     10415, 10420, ('TCP', 'UDP'), 'IPv4 only', 'OOS 10/2026'),
    ('Europe', ('ch.iperf.014.fr',), ('Switzerland / Zurich',), '014.fr', '014.fr',
     15315, 15320, ('TCP', 'UDP'), 'IPv4 only', 'OOS 10/2026'),
    ('Europe', ('iperf.astra.in.ua',), ('Ukraine / Lviv',), 'Astra', '\u0410\u0441\u0442\u0440\u0430',
     5201, 5206, ('TCP', 'UDP'), 'IPv4 and IPv6', 'OK 10/2026'),
    ('Europe', ('iperf.volia.net',), ('Ukraine',), 'Volia', '\u0412\u041e\u041b\u042f',
     5201, 5201, ('TCP', 'UDP'), 'IPv4 only', 'OOS 10/2026'),
    ('Africa', ('iperf.angolacables.co.ao',), ('Angola / Luanda',), 'Angola Cables', 'Angola Cables',
     9200, 9240, ('TCP', 'UDP'), 'IPv4 and IPv6', 'OK 10/2026'),
    ('Asia', ('speedtest.uztelecom.uz',), ('Uzbekistan / Tashkent',), 'Uztelecom', 'Uztelecom',
     5200, 5209, ('TCP', 'UDP'), 'IPv4 and IPv6', 'OK 10/2026'),
    ('Americas', ('iperf.he.net',), ('USA / California',), 'Hurricane Electric', 'he.net',
     5201, 5201, ('TCP', 'UDP'), 'IPv4 and IPv6', 'OK 10/2026'),
)


def unsupported_reason(server):
    if 'IPv4' not in server['ip_versions']:
        return 'This server is IPv6-only; this check currently requires IPv4.'
    if 'TCP' not in server['protocols']:
        return 'This server does not list TCP; this check requires iPerf3 TCP receive traffic.'
    return ''


def catalogue_entries():
    entries = []
    for row_number, row in enumerate(ROWS, 1):
        region, hosts, locations, provider, source_provider, first, last, protocols, family, status = row
        if len(locations) not in (1, len(hosts)) or not 1 <= first <= last <= 65535:
            raise ValueError('Invalid public directory snapshot row')
        for index, hostname in enumerate(hosts):
            versions = ['IPv4'] if family == 'IPv4 only' else ['IPv4', 'IPv6']
            if hostname in ('ping6.online.net', 'ping6-90ms.online.net'):
                versions = ['IPv6']
            location = locations[index] if len(locations) > 1 else locations[0]
            server = {'id': 'scaleway-france' if hostname == 'ping.online.net' else hostname,
                      'hostname': hostname, 'region': region, 'location': location,
                      'label': location + ' - ' + provider, 'provider': provider,
                      'source_provider': source_provider, 'source_row': row_number,
                      'port_first': first, 'port_last': last,
                      'port': 5201 if first <= 5201 <= last else first,
                      'protocols': list(protocols), 'ip_versions': versions,
                      'source_ip_version': family, 'status': status}
            server['unsupported_reason'] = unsupported_reason(server)
            entries.append(server)
    if len({server['id'] for server in entries}) != len(entries):
        raise ValueError('Duplicate public directory snapshot entry')
    return tuple(entries)


PUBLIC_SERVERS = catalogue_entries()


def catalogue_metadata():
    return {'source_url': SOURCE_URL, 'source_date': SOURCE_DATE,
            'live_refresh': False, 'rows': len(ROWS), 'hosts': len(PUBLIC_SERVERS),
            'regions': list(dict.fromkeys(server['region'] for server in PUBLIC_SERVERS))}


def catalogue_page(region, offset):
    if region not in catalogue_metadata()['regions']:
        raise ValueError('Unknown public server region')
    if type(offset) is not int or offset < 0:
        raise ValueError('Invalid public directory page')
    entries = [server for server in PUBLIC_SERVERS if server['region'] == region]
    if offset > len(entries):
        raise ValueError('Invalid public directory page')
    end = min(offset + 8, len(entries))
    return {'region': region, 'servers': entries[offset:end],
            'next_offset': end if end < len(entries) else None}
