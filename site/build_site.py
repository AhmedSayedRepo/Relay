import re, sys, urllib.parse
src, out, lang = sys.argv[1], sys.argv[2], sys.argv[3]
s = open(src, encoding="utf-8").read()
helmet = re.search(r"<helmet>(.*?)</helmet>", s, re.S).group(1)
body = re.search(r"<x-dc>(.*?)</x-dc>", s, re.S).group(1)
body = re.sub(r"<helmet>.*?</helmet>", "", body, flags=re.S)
title = re.search(r"<title>(.*?)</title>", s).group(1)
mark = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><rect width="100" height="100" rx="22" fill="#103e46"/>'
        '<g fill="none" stroke-linecap="round" stroke-linejoin="round" transform="translate(13 13) scale(.74)">'
        '<path d="M14 26h22c10 0 16 6 22 14l8 10" stroke="#f4f6f3" stroke-width="12"/>'
        '<path d="M14 74h22c10 0 16-6 22-14l8-10" stroke="#eda06d" stroke-width="12"/>'
        '<circle cx="78" cy="50" r="12" fill="#35a491"/></g></svg>')
icon = "data:image/svg+xml," + urllib.parse.quote(mark)
ar = lang == "ar"
# Download: hero buttons go to the install section; the install button waits for the signed APK.
body = body.replace('href="{{apkUrl}}"', 'href="#install"')
soon = "رابط التنزيل قريباً" if ar else "Download link coming soon"
install_btn = re.compile(r'(<a class="btn-primary" href="#install" style="align-self: flex-start;[^"]*")>(.*?)</a>', re.S)
body = install_btn.sub(lambda m: m.group(1).replace('<a class="btn-primary" href="#install"', '<span aria-disabled="true"').replace("background: #a95028", "background: #8a9c98") + ">" + soon + "</span>", body, count=1)
if ar:
    body = body.replace("Relay Agent {{version}} · أندرويد 8.0 أو أحدث · ملف APK موقّع يُثبَّت مباشرة", "Relay Agent · أندرويد 8.0 أو أحدث · ملف APK موقّع يُثبَّت مباشرة")
    body = body.replace("الإصدار {{version}} · أندرويد 8.0 أو أحدث · SHA-256 {{apkSha}}", "أندرويد 8.0 أو أحدث · سيُنشر الملف الموقّع مع بصمة SHA-256 للتحقق منه.")
    body = re.sub(r'\s*<a href="mailto:\[CONTACT EMAIL\]"[^>]*>[^<]*</a>', "", body)
    switch = '<a class="navlink" href="/" hreflang="en" lang="en" style="color: #b8ccc8; text-decoration: none">English</a>\n'
else:
    body = body.replace("Relay Agent {{version}} · Android 8.0 or newer · signed APK, installed directly", "Relay Agent · Android 8.0 or newer · signed APK, installed directly")
    body = body.replace("Version {{version}} · Android 8.0 or newer · SHA-256 {{apkSha}}", "Android 8.0 or newer · the signed file will be published with its SHA-256 to verify it.")
    body = re.sub(r'\s*<a href="mailto:\[CONTACT EMAIL\]"[^>]*>[^<]*</a>', "", body)
    switch = '<a class="navlink" href="/ar/" hreflang="ar" lang="ar" style="color: #b8ccc8; text-decoration: none">العربية</a>\n'
body = body.replace('<a class="btn-ghost" href="#install" style="color: #f4f6f3; text-decoration: none; border: 1px solid #4d7a80', switch + '<a class="btn-ghost" href="#install" style="color: #f4f6f3; text-decoration: none; border: 1px solid #4d7a80', 1)
assert "{{" not in body and "[CONTACT" not in body, re.findall(r"\{\{\w+\}\}|\[[A-Z ]+\]", body)
desc = ("يجمع Relay لقطات إيصالات انستاباي ورسائل البنك ويطابقها بالمبلغ والدقيقة. يعمل على خادمك الخاص." if ar else
        "Relay collects InstaPay receipt screenshots and bank SMS, matches them on amount and minute, and flags receipts the bank never confirmed. Self-hosted.")
mobile = ("<style>@media (max-width: 640px){h1{font-size:40px !important}h2{font-size:30px !important}"
          "section > div{padding-left:20px !important;padding-right:20px !important}}"
          "html{scroll-behavior:smooth}</style>")
html = f"""<!doctype html>
<html lang="{'ar' if ar else 'en'}" dir="{'rtl' if ar else 'ltr'}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="{desc}">
<link rel="icon" href="{icon}">
<link rel="alternate" hreflang="en" href="/">
<link rel="alternate" hreflang="ar" href="/ar/">
{helmet.strip()}
{mobile}
</head>
<body>
{body.strip()}
</body>
</html>
"""
open(out, "w", encoding="utf-8").write(html)
print(out, len(html))
