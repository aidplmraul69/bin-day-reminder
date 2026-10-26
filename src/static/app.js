/* All dynamic strings enter the DOM through textContent or form values. */
'use strict';
let config, revision, csrf;
const $ = id => document.getElementById(id);
function node(tag, parent, text, cls) {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = text;
  if (cls) el.className = cls;
  parent.append(el);
  return el;
}
function button(parent, title, action, cls = 'secondary') {
  const el = node('button', parent, title, cls);
  el.type = 'button'; el.addEventListener('click', action); return el;
}
function field(parent, label, value, update, type = 'text', opts = {}) {
  const wrap = node('label', parent, undefined, 'field');
  node('span', wrap, label);
  const el = node('input', wrap);
  el.type = type; el.value = value ?? '';
  if (opts.min !== undefined) el.min = String(opts.min);
  if (opts.max !== undefined) el.max = String(opts.max);
  el.addEventListener('input', () => update(type === 'number' ? Number(el.value) : el.value));
  return el;
}
function select(parent, label, value, options, update) {
  const wrap = node('label', parent, undefined, 'field'); node('span', wrap, label);
  const el = node('select', wrap);
  for (const [key, title] of options) {
    const option = node('option', el, title); option.value = String(key);
  }
  el.value = String(value);
  el.addEventListener('change', () => update(el.value));
  return el;
}
function message(text, bad = false) {
  $('message').textContent = text;
  $('message').className = bad ? 'error' : 'success';
}
function id() { return crypto.randomUUID(); }
function biweeklyDefault(today = new Date()) {
  const anchor_date = [today.getFullYear(), String(today.getMonth() + 1).padStart(2, '0'), String(today.getDate()).padStart(2, '0')].join('-');
  return {kind: 'biweekly', weekday: (today.getDay() + 6) % 7, anchor_date};
}
function anchorOnWeekday(anchor, weekday) {
  const [year, month, day] = anchor.split('-').map(Number);
  const date = new Date(Date.UTC(year, month - 1, day));
  if (Number.isNaN(date.getTime())) return anchor;
  const oldWeekday = (date.getUTCDay() + 6) % 7;
  date.setUTCDate(date.getUTCDate() + weekday - oldWeekday);
  return date.toISOString().slice(0, 10);
}
function render() {
  for (const name of ['general', 'devices', 'collections']) $(name).replaceChildren();
  field($('general'), 'Timezone (IANA)', config.timezone, v => config.timezone = v);
  const devices = $('devices');
  if (!config.devices.length) node('p', devices, 'No Cast displays yet. Add one to show pickup reminders.', 'empty');
  for (const d of config.devices) {
    const card = node('div', devices, undefined, 'device-card');
    const row = node('div', card, undefined, 'grid three');
    field(row, 'Device UUID', d.uuid, v => {
      const previous = d.uuid;
      for (const c of config.collections) for (const r of c.reminders)
        r.device_uuids = r.device_uuids.map(uuid => uuid === previous ? v : uuid);
      d.uuid = v;
    });
    field(row, 'Display label', d.label, v => d.label = v);
    field(row, 'Known host (optional)', d.known_host || '', v => { if (v) d.known_host = v; else delete d.known_host; });
    button(card, 'Remove device', () => {
      config.devices = config.devices.filter(x => x !== d);
      for (const c of config.collections) for (const r of c.reminders) r.device_uuids = r.device_uuids.filter(uuid => uuid !== d.uuid);
      render();
    }, 'danger');
  }
  const collections = $('collections');
  if (!config.collections.length) node('p', collections, 'No pickup schedules yet. Add a garbage or recycling collection day to get started.', 'empty');
  for (const c of config.collections) {
    const card = node('article', collections, undefined, 'collection');
    const head = node('div', card, undefined, 'card-head');
    node('h3', head, c.label || 'Untitled pickup schedule');
    button(head, 'Delete collection', () => { config.collections = config.collections.filter(x => x !== c); render(); }, 'danger');
    const grid = node('div', card, undefined, 'grid two');
    field(grid, 'Pickup schedule label', c.label, v => { c.label = v; head.querySelector('h3').textContent = v || 'Untitled pickup schedule'; });
    field(grid, 'Bin type / category', c.category, v => c.category = v);
    field(grid, 'Reminder message', c.text, v => c.text = v);
    field(grid, 'Accent color', c.color, v => c.color = v, 'color');
    const rec = node('div', card, undefined, 'recurrence');
    node('h4', rec, 'Collection day recurrence');
    const recGrid = node('div', rec, undefined, 'grid three');
    select(recGrid, 'Frequency', c.recurrence.kind, [['weekly','Weekly'],['biweekly','Every two weeks'],['monthly','Monthly']], kind => {
      c.recurrence = kind === 'monthly' ? {kind, day_of_month: 1} : kind === 'biweekly' ? biweeklyDefault() : {kind, weekday: 0}; render();
    });
    if (c.recurrence.kind !== 'monthly') select(recGrid, 'Weekday', c.recurrence.weekday,
      [['0','Monday'],['1','Tuesday'],['2','Wednesday'],['3','Thursday'],['4','Friday'],['5','Saturday'],['6','Sunday']], v => {
        const weekday = Number(v);
        if (c.recurrence.kind === 'biweekly') c.recurrence.anchor_date = anchorOnWeekday(c.recurrence.anchor_date, weekday);
        c.recurrence.weekday = weekday;
        render();
      });
    if (c.recurrence.kind === 'biweekly') field(recGrid, 'Anchor date', c.recurrence.anchor_date, v => c.recurrence.anchor_date = v, 'date');
    if (c.recurrence.kind === 'monthly') field(recGrid, 'Day of month', c.recurrence.day_of_month, v => c.recurrence.day_of_month = v, 'number', {min:1,max:31});
    const title = node('div', card, undefined, 'section-head reminders-title'); node('h4', title, 'Reminder timing');
    button(title, '+ Add reminder', () => { c.reminders.push({id:id(), lead_days:1, start_time:'18:00', repeat_for_minutes:60, every_minutes:15, display_seconds:30, device_uuids:[]}); render(); });
    if (!c.reminders.length) node('p', card, 'No reminders yet.', 'empty');
    for (const r of c.reminders) {
      const entry = node('div', card, undefined, 'reminder');
      const row = node('div', entry, undefined, 'grid three');
      field(row, 'Days before pickup', r.lead_days, v => r.lead_days = v, 'number', {min:0});
      field(row, 'Start time', r.start_time, v => r.start_time = v, 'time');
      field(row, 'Repeat for (minutes)', r.repeat_for_minutes, v => r.repeat_for_minutes = v, 'number', {min:0});
      field(row, 'Every (minutes)', r.every_minutes, v => r.every_minutes = v, 'number', {min:1});
      field(row, 'Display (seconds)', r.display_seconds, v => r.display_seconds = v, 'number', {min:1});
      const targets = node('fieldset', entry, undefined, 'targets'); node('legend', targets, 'Target devices (choose multiple)');
      if (!config.devices.length) node('p', targets, 'Add a device first.', 'empty');
      for (const d of config.devices) {
        const label = node('label', targets, undefined, 'target');
        const check = node('input', label); check.type = 'checkbox'; check.checked = r.device_uuids.includes(d.uuid);
        node('span', label, d.label + ' · ' + d.uuid);
        check.addEventListener('change', () => { r.device_uuids = check.checked ? [...new Set([...r.device_uuids, d.uuid])] : r.device_uuids.filter(x => x !== d.uuid); });
      }
      button(entry, 'Remove reminder', () => { c.reminders = c.reminders.filter(x => x !== r); render(); }, 'danger');
    }
  }
}
async function api(path, init) {
  const response = await fetch(path, {credentials:'same-origin', cache:'no-store', ...init});
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || 'Request failed');
  return body;
}
async function init() {
  try {
    const [data, token] = await Promise.all([api('/api/config'), api('/api/csrf')]);
    config = data.config; revision = data.revision; csrf = token.csrf_token; render();
  } catch (e) { message('Unable to open configuration: ' + e.message, true); }
}
$('add-device').addEventListener('click', () => { if (!config) return; config.devices.push({uuid:id(),label:'New screen'}); render(); });
$('add-collection').addEventListener('click', () => { if (!config) return; config.collections.push({id:id(),label:'New pickup schedule',category:'Garbage',text:'Garbage pickup is coming up. Put your bin out.',color:'#56c3a6',recurrence:{kind:'weekly',weekday:0},reminders:[]}); render(); });
$('discover').addEventListener('click', async () => {
  try {
    const result = await api('/api/discover'); const area = $('candidates'); area.replaceChildren();
    node('h4', area, 'Discovered candidates (not added)');
    if (!result.devices.length) node('p', area, 'No devices found.', 'empty');
    for (const d of result.devices) {
      const row = node('div', area, undefined, 'candidate');
      node('span', row, String(d.label || d.uuid) + ' · ' + String(d.uuid));
      button(row, 'Add', () => { if (!config.devices.some(x => x.uuid === d.uuid)) config.devices.push({uuid:String(d.uuid),label:String(d.label || d.uuid),...(d.known_host ? {known_host:String(d.known_host)} : {})}); area.replaceChildren(); render(); });
    }
  } catch (e) { message('Discovery failed: ' + e.message, true); }
});
$('save').addEventListener('click', async () => {
  if (!config) return;
  try {
    const result = await api('/api/config', {method:'PUT',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify({config,revision})});
    revision = result.revision; config = result.config; message('Configuration saved.'); render();
  } catch (e) { message('Save failed: ' + e.message, true); }
});
init();
