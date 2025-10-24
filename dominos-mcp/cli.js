// Simple JSON CLI for Domino's API (ESM)
// Usage examples:
//   node cli.js stores --address "1600 Pennsylvania Ave NW, Washington, DC 20500"
//   node cli.js menu --store 12345
//   node cli.js price --store 12345 --address "..." --first Test --last User --phone 555-0100 --email t@example.com --items '[{"code":"14SCREEN","qty":1}]'

import { Address, NearbyStores, Store, Menu, Order, Customer, Item, Payment } from 'dominos';

function parseArgs(argv) {
  const args = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith('--')) {
      const key = a.slice(2);
      const val = argv[i + 1] && !argv[i + 1].startsWith('--') ? argv[++i] : true;
      args[key] = val;
    }
  }
  return args;
}

function jsonOut(ok, data) {
  const out = { ok, ...data };
  console.log(JSON.stringify(out));
}

async function cmdStores(args) {
  const addressStr = args.address || '';
  if (!addressStr) return jsonOut(false, { error: 'address is required' });
  try {
    const addr = new Address(addressStr);
    const nearby = await new NearbyStores(addr, 'Delivery');
    const stores = (nearby.stores || []).map((s) => ({
      StoreID: s.StoreID,
      AddressDescription: s.AddressDescription,
      MinDistance: s.MinDistance,
      IsOnlineCapable: s.IsOnlineCapable,
      IsDeliveryStore: s.IsDeliveryStore,
      IsOpen: s.IsOpen,
      ServiceIsOpen: s.ServiceIsOpen,
    }));
    // Build a canonical address string: "street, city, region, postalCode"
    const a = nearby.address || {};
    const street = a.street || [a.streetNumber, a.streetName, a.unitType, a.unitNumber].filter(Boolean).join(' ').trim();
    const addrText = [street, a.city, a.region, a.postalCode].filter(Boolean).join(', ');
    jsonOut(true, { address: addrText || addressStr, stores });
  } catch (e) {
    jsonOut(false, { error: String(e?.message || e) });
  }
}

function groupMenu(variantMap) {
  const groups = { pizzas: [], sides: [], drinks: [], desserts: [], other: [] };
  const keys = Object.keys(variantMap || {});
  const toName = (v) => v?.name || v?.Name || '';
  for (const code of keys) {
    const v = variantMap[code];
    const name = toName(v);
    const low = name.toLowerCase();
    const sizeHint = (code.match(/(10|12|14|16|18)/) || [])[0] || '';
    const entry = { code, name, sizeHint };
    if (/pizza|hand tossed|brooklyn|pan|screen|thin/i.test(name) || /SCREEN|HAND|BROOKLYN|PAN/i.test(code)) {
      groups.pizzas.push(entry);
    } else if (/drink|coke|pepsi|sprite|soda|beverage/i.test(name)) {
      groups.drinks.push(entry);
    } else if (/dessert|cookie|brownie|lava|marble/i.test(name)) {
      groups.desserts.push(entry);
    } else if (/bread|wing|pasta|sandwich|salad/i.test(name)) {
      groups.sides.push(entry);
    } else {
      groups.other.push(entry);
    }
  }
  // sort by name
  for (const k of Object.keys(groups)) groups[k].sort((a, b) => a.name.localeCompare(b.name));
  return groups;
}

async function cmdMenu(args) {
  const storeID = args.store || args.storeID || '';
  if (!storeID) return jsonOut(false, { error: 'store is required' });
  try {
    const store = await new Store(storeID, 'en');
    const menu = await new Menu(storeID, 'en');
    const groups = groupMenu(menu.menu.variants || {});
    jsonOut(true, {
      store: { id: storeID, name: store.info?.StoreName },
      groups,
    });
  } catch (e) {
    jsonOut(false, { error: String(e?.message || e) });
  }
}

async function cmdPrice(args) {
  const storeID = args.store || args.storeID || '';
  const addressStr = args.address || '';
  const first = args.first || 'Test';
  const last = args.last || 'User';
  const phone = (args.phone || '555-0100').replace(/\-/g, '');
  const email = args.email || 'test@example.com';
  const itemsRaw = args.items || '[]';
  const service = (args.service || args.method || 'Delivery');
  let items;
  try {
    items = JSON.parse(itemsRaw);
  } catch (e) {
    return jsonOut(false, { error: 'items must be a JSON array' });
  }
  if (!storeID || !addressStr || !Array.isArray(items) || items.length === 0) {
    return jsonOut(false, { error: 'store, address, and items are required' });
  }
  let order;
  try {
    const customer = new Customer({ address: addressStr, firstName: first, lastName: last, phone, email });
    order = new Order(customer);
    order.storeID = storeID;
    order.serviceMethod = service;
    for (const it of items) {
      const code = it.code;
      const qty = Number(it.qty || 1);
      const options = it.options || {};
      if (!code) continue;
      const item = new Item({ code, qty, options });
      order.addItem(item);
    }
    let stage = 'validate';
    await order.validate();
    stage = 'price';
    await order.price();
    const pricedItems = order.products.map((p) => ({ code: p.code, qty: p.qty }));
    jsonOut(true, {
      amountsBreakdown: order.amountsBreakdown,
      items: pricedItems,
      storeID,
      serviceMethod: order.serviceMethod,
    });
  } catch (e) {
    // try to include structured responses if available
    let details = {};
    try {
      if (typeof e === 'object' && e) {
        details.errorName = e.name;
        details.stack = e.stack;
      }
    } catch {}
    try {
      // order may be in closure scope
      details.validationResponse = (typeof order !== 'undefined' && order?.validationResponse) || undefined;
      details.priceResponse = (typeof order !== 'undefined' && order?.priceResponse) || undefined;
      details.serviceMethod = (typeof order !== 'undefined' && order?.serviceMethod) || service;
      details.storeID = storeID;
      details.address = addressStr;
      details.items = items;
    } catch {}
    jsonOut(false, { error: String(e?.message || e), ...details });
  }
}

async function cmdPlace(args) {
  const storeID = args.store || args.storeID || '';
  const addressStr = args.address || '';
  const first = args.first || 'Test';
  const last = args.last || 'User';
  const phone = (args.phone || '555-0100').replace(/\-/g, '');
  const email = args.email || 'test@example.com';
  const itemsRaw = args.items || '[]';
  const cardNumber = (args.cardNumber || '').replace(/\D/g, '');
  const exp = args.exp || '';
  const cvv = args.cvv || '';
  const postal = args.postal || '';
  const tip = Number(args.tip || 0);

  let items;
  try {
    items = JSON.parse(itemsRaw);
  } catch (e) {
    return jsonOut(false, { error: 'items must be a JSON array' });
  }
  if (!storeID || !addressStr || !Array.isArray(items) || items.length === 0) {
    return jsonOut(false, { error: 'store, address, and items are required' });
  }
  if (!cardNumber || !exp || !cvv || !postal) {
    return jsonOut(false, { error: 'cardNumber, exp, cvv, and postal are required' });
  }

  try {
    const customer = new Customer({ address: addressStr, firstName: first, lastName: last, phone, email });
    const order = new Order(customer);
    order.storeID = storeID;
    for (const it of items) {
      const code = it.code;
      const qty = Number(it.qty || 1);
      const options = it.options || {};
      if (!code) continue;
      const item = new Item({ code, qty, options });
      order.addItem(item);
    }
    await order.validate();
    await order.price();
    const pay = new Payment({
      amount: order.amountsBreakdown.customer,
      number: cardNumber,
      expiration: exp,
      securityCode: cvv,
      postalCode: postal,
      tipAmount: tip,
    });
    order.payments.push(pay);
    const res = await order.place();
    jsonOut(true, { result: res, placeResponse: order.placeResponse });
  } catch (e) {
    jsonOut(false, { error: String(e?.message || e) });
  }
}

async function main() {
  const [,, cmd, ...rest] = process.argv;
  const args = parseArgs(rest);
  if (!cmd) return jsonOut(false, { error: 'missing command (stores|menu|price)' });
  if (cmd === 'stores') return cmdStores(args);
  if (cmd === 'menu') return cmdMenu(args);
  if (cmd === 'price') return cmdPrice(args);
  if (cmd === 'place') return cmdPlace(args);
  return jsonOut(false, { error: `unknown command ${cmd}` });
}

main();
