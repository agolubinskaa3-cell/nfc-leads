[app]
title = NFC Leads
package.name = nfcleads
package.domain = org.nfcleads
source.dir = .
source.include_exts = py
version = 1.0
requirements = python3,kivy==2.3.0,requests,urllib3,idna,charset-normalizer,certifi,openpyxl,et_xmlfile,sqlite3,pyjnius,android
orientation = portrait
fullscreen = 0
android.permissions = INTERNET,WRITE_EXTERNAL_STORAGE,READ_EXTERNAL_STORAGE
android.api = 33
android.minapi = 24
android.archs = arm64-v8a
android.accept_sdk_license = True

[buildozer]
log_level = 2
warn_on_root = 1
