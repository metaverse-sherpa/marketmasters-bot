---
name: Alpaca Paper Trading Account
description: Credentials and context for the Alpaca paper trading account used in this project
type: project
---

This project is focused on paper trading using Alpaca. Credentials are stored in `/Users/johngiles/projects/claudetrade/.alpaca.env`.

**Endpoint:** https://paper-api.alpaca.markets/v2  
**Key:** PKLV5DFFHO4CBENVI763GOPHLT  
**Secret:** 6nRZAvfdDeh64YeN57CgQaXGhaY1mWeVC9bnazCELwqp  

**Why:** User will be doing many trades in this folder and doesn't want to re-supply credentials each session.  
**How to apply:** Always read credentials from `.alpaca.env` or use the values above when making Alpaca API calls in this project. Use the paper trading endpoint, not live.
