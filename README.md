# ✈️ Pairing Parser Web App

A lightweight, self-hosted **PDF pairing schedule parser and viewer** built for airline pilots.  
It extracts and visualizes monthly pairing PDFs (final or prelim) into structured, searchable tables — optimized for iPad Pro and desktop browsers.

---

## 🌐 Live Demo

Visit [https://beta.pairingviewer.com](https://beta.pairingviewer.com)

---

## 🚀 Features

- 📄 Upload airline pairing PDF (supports both **Final** and **Prelim**)
- ⚡ Fast, parallelized PDF-to-text extraction with caching (SHA-256 based)
- 🧠 Smart parser: detects TAFB, per diem, layovers, report/release times, and more
- 📱 Optimized UI for **iPad Pro M4 landscape**
- 🌙 Night mode + persistent column layout with Tabulator
- 🔍 Instant search, filter by redeye, commutable, lazy, weekdays, etc.
- 💾 Offline caching: reuploads of identical files load instantly

---

## 🏗️ Architecture

| Layer | Stack |
|-------|--------|
| Frontend | HTML5 + Tabulator 5.x + Vanilla JS |
| Backend | Flask (Python 3.11+) + PyMuPDF |
| Cache | JSON Gzip (local `cache/` directory) |
| Hosting | Any VPS (Nginx + Gunicorn + Certbot) |

---

