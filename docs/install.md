# Install and setup

Get Inventory running on a protoAgent agent. Every setting is described in
[configuration.md](configuration.md).

## Requirements

- **protoAgent 0.148.0 or later.** The manifest declares `min_protoagent_version: 0.148.0` (the host's
  instance-scoped plugin store), and the loader refuses the plugin on older cores. Some features need a newer
  host and fall back gracefully without one:

  | Feature | Needs | Without it |
  |---|---|---|
  | A scanned label opens the bin or item in the console | protoAgent 0.227.0 (plugin-view deep links, [#4712](https://github.com/protoLabsAI/protoAgent/pull/4712)) | The label opens a page that names the bin or item and links to the console |
  | **Ask for a re-price** drafts a message in chat | protoAgent 0.206.0 (the chat-compose bridge) | Use the agent in chat yourself |
  | Label sheets open in your browser from the desktop app | protoAgent desktop 0.157.0 (it hands plugin links to the system browser) | Copy the sheet link from the print dialog |

- **A vision-capable model** for scanning a box and for listing drafts: those turns hand the agent your
  photos. Everything else works on any model.
- **The [ebay plugin](https://github.com/protoLabsAI/ebay-plugin)**, for prices from eBay sold listings and
  for fee estimates (`ebay_price_check`, `ebay_net_proceeds`). Without it you can still set prices by hand,
  with a basis.

## Install

1. **Install it.** In a running protoAgent: **Console → Plugins → Install from URL**, with
   `https://github.com/protoLabsAI/inventory-plugin`. Or from the shell:

   ```sh
   python -m server plugin install https://github.com/protoLabsAI/inventory-plugin
   ```

2. **Turn it on** in **Console → Plugins**. It ships disabled. In YAML:

   ```yaml
   plugins:
     enabled: [inventory]
   ```

3. **Set the basics** in **Settings → Plugins → Inventory**:
   - **Workspace directory**: where relative CSV paths resolve. Point it at the agent workspace.
   - **Weekly review**: on, with your timezone, if you want a Monday re-price and P&L.
   - **Bin label address**: leave it blank unless labels need a different address than the agent's own. Set
     it before you print your first sheet.

The database is created on first use in the host's instance-scoped plugin store, so each agent and the dev
sandbox get their own.

## First steps

1. **Make bins.** Open **Inventory** from the console's rail, go to **Bins** and create a few (B01, B02…).
   Print their labels from **Print labels** and stick one on each box or shelf.
2. **Bring stock in.** Either **Scan a box** for a box you've bought, or **More → Import CSV** to load a
   spreadsheet you already keep. The import matches common headers (`inventory_id`, `item`, `lot_id`,
   `target_price_usd`, `status`, `sold_price_usd`…) and reports what it couldn't place.
3. **Label and put away.** Print item labels for the pieces, stick them on, then **Bins → Put away** and scan
   each piece, then the bin.
4. **Add your packaging** under **More → Packaging** if you want packed weights and box sizes on listings.
   None ship with the plugin.

## On a phone

Use the console from your Home Screen (the protoAgent web app) through your tailnet, the same way you reach
it in a browser.

- **Scan inside the app.** Use **Scan label** in Inventory. On an iPhone, a QR scanned with the Camera app
  always opens in Safari, never in a Home Screen web app, and Safari keeps its own sign-in. A label opened
  that way shows its id and how to open it in the app. A Safari that's already signed in to the console goes
  straight to the bin.
- **Live camera or a photo.** Put away, Check a bin and Scan label use a live viewfinder where the browser
  allows it (through a fleet hub it does). Otherwise they use your phone's camera to take a photo. A sharp
  photo reads a label from about 45 cm away. The live camera needs the label closer, because a video frame
  has fewer pixels than a photo.
- **The desktop app** uses photos for scanning, because its frames can't use the camera.
