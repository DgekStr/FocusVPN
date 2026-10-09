import base64
import html
import json
from pathlib import Path
from urllib.parse import quote
from urllib.parse import urlsplit, urlunsplit

from happ_users import parse_expiry

STATE_PATH = Path('/etc/sing-box-admin/happ-server.json')
HAPP_DIRECT_SITES = (
    'domain:mtalk.google.com',
    'domain:push.apple.com',
    'domain:api.push.apple.com',
    'domain:push-apple.com.akadns.net',
    'domain:courier.push.apple.com',
    'domain:yandex.com',
    'domain:yandex.net',
    'domain:yandex.ru',
    'domain:ya.ru',
    'domain:yastatic.net',
    'domain:mail.ru',
    'domain:my.mail.ru',
    'domain:e.mail.ru',
    'domain:mrgcdn.ru',
    'domain:my.com',
    'domain:vk.com',
    'domain:vk.ru',
    'domain:vk.me',
    'domain:vkontakte.ru',
    'domain:vkuservideo.net',
    'domain:vkuseraudio.net',
    'domain:vk-cdn.net',
    'domain:vk.company',
    'domain:userapi.com',
    'domain:vk.cc',
    'domain:vkuser.net',
    'domain:vk.video',
    'domain:vk-portal.net',
    'domain:vk-portal.org',
    'domain:vk-apps.com',
    'domain:vkpay.ru',
    'domain:vkforms.ru',
    'domain:vkmessenger.com',
    'domain:ok.ru',
    'domain:okcdn.ru',
    'domain:ok.me',
    'domain:odkl.ru',
    'domain:odnoklassniki.ru',
    'domain:mycdn.me',
    'domain:max.ru',
    'domain:oneme.ru',
    'domain:media.max.ru',
    'domain:static.max.ru',
    'domain:messenger.max.ru',
    'domain:st.max.ru',
    'domain:ic1.max.ru',
    'domain:ic2.max.ru',
    'domain:ic3.max.ru',
    'domain:ic4.max.ru',
    'domain:i.oneme.ru',
    'domain:ws-api.oneme.ru',
    'domain:calls.okcdn.ru',
    'domain:sberbank.ru',
    'domain:sber.ru',
    'domain:sbrf.ru',
    'domain:online.sberbank.ru',
    'domain:id.sber.ru',
    'domain:tinkoff.ru',
    'domain:tbank.ru',
    'domain:tcsbank.ru',
    'domain:cdn-tinkoff.ru',
    'domain:t-bank-app.ru',
    'domain:acdn.tinkoff.ru',
    'domain:api.tinkoff.ru',
    'domain:alfabank.ru',
    'domain:vtb.ru',
    'domain:rosbank.ru',
    'domain:gazprombank.ru',
    'domain:raiffeisen.ru',
    'domain:psbank.ru',
    'domain:open.ru',
    'domain:nspk.ru',
    'domain:qr.nspk.ru',
    'domain:mironline.ru',
    'domain:homecredit.ru',
    'domain:sovcombank.ru',
    'domain:gosuslugi.ru',
    'domain:nalog.gov.ru',
    'domain:mos.ru',
    'domain:government.ru',
    'domain:kremlin.ru',
    'domain:ozon.ru',
    'domain:wildberries.ru',
    'domain:wb.ru',
    'domain:avito.ru',
    'domain:avito.st',
    'domain:vkusvill.ru',
    'domain:2gis.com',
    'domain:2gis.ru',
    'domain:dns-shop.ru',
    'domain:mvideo.ru',
    'domain:citilink.ru',
    'domain:lamoda.ru',
    'domain:sbermegamarket.ru',
    'domain:mts.ru',
    'domain:megafon.ru',
    'domain:beeline.ru',
    'domain:tele2.ru',
    'domain:rt.ru',
    'domain:rostelecom.ru',
    'domain:mangabuff.ru',
    'domain:api.ipify.org',
    'domain:checkip.amazonaws.com',
    'domain:ifconfig.me',
    'domain:ip.mail.ru',
    'domain:ipv4-internet.yandex.net',
    'domain:ipv6-internet.yandex.net',
    'domain:101hotels.com',
    'domain:alfa.me',
    'domain:appsflyersdk.com',
    'domain:avito.com',
    'domain:beget.com',
    'domain:championat.com',
    'domain:chizhik.club',
    'domain:cian.com',
    'domain:drweb.com',
    'domain:eda.yandex',
    'domain:fon.bet',
    'domain:gett.com',
    'domain:gismeteo.com',
    'domain:go.yandex',
    'domain:group-ib.com',
    'domain:icq.com',
    'domain:icq.im',
    'domain:icq.net',
    'domain:interfax.com',
    'domain:ixbt.com',
    'domain:joom.com',
    'domain:kaspersky.com',
    'domain:kontur.host',
    'domain:lavka.yandex',
    'domain:lenta.com',
    'domain:livejournal.com',
    'domain:maps.me',
    'domain:match.tv',
    'domain:mediascope.net',
    'domain:megamarket.tech',
    'domain:megogo.net',
    'domain:moex.com',
    'domain:more.tv',
    'domain:mradx.net',
    'domain:mts.com',
    'domain:myicq.com',
    'domain:mytarget.com',
    'domain:mytracker.com',
    'domain:mytrackerapi.com',
    'domain:okko.tv',
    'domain:onetwotrip.com',
    'domain:ostin.com',
    'domain:otkritie.com',
    'domain:ozon.st',
    'domain:ozon.travel',
    'domain:ozoncloud.net',
    'domain:ozonusercontent.com',
    'domain:pobeda.aero',
    'domain:positive-tech.com',
    'domain:premier.one',
    'domain:ptsecurity.com',
    'domain:qiwi.com',
    'domain:ren.tv',
    'domain:rt.com',
    'domain:russian.rt.com',
    'domain:selectel.com',
    'domain:sputnikglobe.com',
    'domain:sputniknews.com',
    'domain:tamtam.chat',
    'domain:tilda.cc',
    'domain:tilda.ws',
    'domain:tildaapi.com',
    'domain:timeweb.com',
    'domain:toloka.ai',
    'domain:uralairlines.com',
    'domain:vk-cdn.me',
    'domain:vk.cloud',
    'domain:vk.link',
    'domain:vkplay.live',
    'domain:wbstatic.net',
    'domain:wildberries.kz',
    'domain:ya.cc',
    'domain:yadi.sk',
    'domain:yandex.az',
    'domain:yandex.by',
    'domain:yandex.cloud',
    'domain:yandex.com.tr',
    'domain:yandex.ee',
    'domain:yandex.fr',
    'domain:yandex.kz',
    'domain:yandex.lt',
    'domain:yandex.lv',
    'domain:yandex.md',
    'domain:yandex.tj',
    'domain:yandex.tm',
    'domain:yandex.ua',
    'domain:yandex.uz',
    'domain:yandexadexchange.net',
    'domain:yandexcloud.net',
    'domain:yango.com',
    'domain:yappy.media',
    'domain:youdrive.today',
    'domain:zvuk.com',
    'domain:focuslens.dev',
)
DEFAULT_SUBSCRIPTION_TITLE = '\U0001f5a7 FocusVPN'
SUBSCRIPTION_ANNOUNCEMENT = (
    '🔒Это частный VPN сервер, для работы команды разработчиков focuslens.dev. '
    'Если вы здесь оказались - это не случайно ❤️'
)
MAX_SUBSCRIPTION_TITLE_LENGTH = 80
MAX_SUBSCRIPTION_ANNOUNCEMENT_LENGTH = 2000


def load_state():
    return json.loads(STATE_PATH.read_text(encoding='utf-8'))


def happ_add_link(link):
    return 'happ://add/' + (link if link.startswith(('https://', 'http://')) else quote(link, safe=''))


def public_vless_link():
    return load_state()['link']


def public_happ_link():
    return happ_add_link(public_vless_link())


def vless_link_for_subscription(link, subscription_url):
    parsed_link = urlsplit(link)
    public_host = urlsplit(subscription_url).hostname
    if parsed_link.scheme != 'vless' or not parsed_link.hostname or not public_host:
        raise ValueError('Не удалось определить публичный VLESS host.')
    if ':' in public_host:
        public_host = '[' + public_host + ']'
    userinfo, separator, _ = parsed_link.netloc.rpartition('@')
    authority = (userinfo + separator if separator else '') + public_host
    if parsed_link.port:
        authority += ':' + str(parsed_link.port)
    return urlunsplit((parsed_link.scheme, authority, parsed_link.path, parsed_link.query, parsed_link.fragment))


def validate_subscription_content(title, announcement):
    title = str(title or '').strip()
    announcement = str(announcement or '').replace('\r\n', '\n').replace('\r', '\n').strip()
    if not title or len(title) > MAX_SUBSCRIPTION_TITLE_LENGTH or any(ord(char) < 32 for char in title):
        raise ValueError('Заголовок HAPP должен содержать от 1 до 80 символов без управляющих символов.')
    if len(announcement) > MAX_SUBSCRIPTION_ANNOUNCEMENT_LENGTH or any(
        ord(char) < 32 and char not in '\n\t' for char in announcement
    ):
        raise ValueError('Текст объявления HAPP слишком длинный или содержит управляющие символы.')
    return title, announcement


def subscription_content(user, traffic, information_url=None, title=DEFAULT_SUBSCRIPTION_TITLE, announcement=SUBSCRIPTION_ANNOUNCEMENT):
    download = max(0, int(traffic.get('download_bytes', 0)))
    upload = max(0, int(traffic.get('upload_bytes', 0)))
    userinfo = f'upload={upload}; download={download}; total=0'
    expiry = parse_expiry(user.get('expires_at'))
    if expiry is not None:
        userinfo += '; expire=' + str(int(expiry.timestamp()))
    title, announcement = validate_subscription_content(title, announcement)
    encoded_title = base64.b64encode((title + ' ' + user['name'])[:25].encode('utf-8')).decode('ascii')
    headers = {'subscription-userinfo': userinfo, 'profile-update-interval': '1', 'profile-title': 'base64:' + encoded_title}
    downloaded = f'{download / (1024 ** 3):.2f} ГБ' if download >= 1024 ** 3 else f'{download / (1024 ** 2):.2f} МБ'
    announcement_text = (announcement + '\n' if announcement else '') + 'Скачано: ' + downloaded + ' / ∞'
    headers['announce'] = 'base64:' + base64.b64encode(announcement_text.encode('utf-8')).decode('ascii')
    if information_url:
        headers['profile-web-page-url'] = information_url
    routing_profile = {'Name': 'FocusVPN Direct', 'GlobalProxy': 'true', 'LastUpdated': '1791417601', 'DirectSites': list(HAPP_DIRECT_SITES)}
    routing_link = 'happ://routing/onadd/' + base64.b64encode(json.dumps(routing_profile, separators=(',', ':')).encode('utf-8')).decode('ascii')
    body = ''.join(f'#{key}: {value}\n' for key, value in headers.items()) + routing_link + '\n' + user['link'] + '\n'
    return body.encode('utf-8'), headers


def subscription_information_page(title=DEFAULT_SUBSCRIPTION_TITLE, announcement=SUBSCRIPTION_ANNOUNCEMENT):
    title, announcement = validate_subscription_content(title, announcement)
    paragraphs = ''.join('<p>' + html.escape(paragraph) + '</p>' for paragraph in announcement.split('\n'))
    escaped_title = html.escape(title)
    return '<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>' + escaped_title + '</title><style>body{margin:0;background:#f4f7fa;color:#17252e;font:18px/1.65 Georgia,serif}main{max-width:680px;margin:48px auto;padding:0 24px}h1{font-size:28px}p{text-align:justify;overflow-wrap:anywhere;hyphens:auto}@media(max-width:480px){main{margin:24px auto;padding:0 18px}}</style></head><body><main><h1>' + escaped_title + '</h1>' + paragraphs + '</main></body></html>'
