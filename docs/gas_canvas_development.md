# GAS Canvas Development

This page is for developers running or modifying GAS Canvas locally. For the
end-user workflow manual, see [Use GAS Canvas](gas_canvas.md).

## Run Locally

From the `gas_canvas` folder:

```powershell
npm install
npm run dev
```

Open:

```text
http://localhost:3000
```

If `server.ts` changes, restart the Canvas server so the local API helpers are
reloaded.

## Local Preview API Helpers

The Canvas server includes local preview helpers:

- `/api/parse-gpkg`: downloads and parses a GeoPackage into GeoJSON for Map
  View.
- `/api/fetch-artifact`: fetches remote artifacts through the local app for
  HTML and generic artifact viewing.

These routes are intended for interactive preview and inspection. They should
not be treated as permanent data storage.

## Related Pages

- [Use GAS Canvas](gas_canvas.md)
- [GAS Interfaces](gas_interfaces.md)
- [GAS Server Architecture](gas_server_architecture.md)
