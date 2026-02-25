import qrcode as qr
url="https://myschool-rw-web.onrender.com/sos/parentportal"
qr.make(url).save("qr_code.png")