# GT3 Car Maker

A Blender add-on for making **Gran Turismo 3  cars**.
# Rrequirements
- Blender 4.2 or newer (No Info if it works with newer Blender versions.)
- Pure Python. The only extra library is numpy, and Blender already ships it. No .exe files, no outside tools.
---
## 1. Install

1. Copy the whole `gt3_car_maker` folder into Blender's add-ons folder, e.g.
   `%APPDATA%\Blender Foundation\Blender\4.5\scripts\addons\gt3_car_maker`, or just install the release zip through
   *Edit > Preferences > Add-ons > Install from Disk*.
2. Enable **GT3 Car Maker (from scratch)**.
3. In its preferences, set **GT3 database folder** to the game's `data/database` folder (the one with `carcolor.db` in
   it). Until you do, the preferences and the sidebar shout **"No database path!"** at you in red. Don't worry tho, it
   fills itself in the first time you import or export a car inside a game data folder.

Restart Blender after updating the add-on.

## 2. Quick start

1. 3D view, sidebar (N) > **GT3 Car** tab. Type a car code (the car you're replacing, e.g. `ar0006`) and hit
   **Race car**.
2. Model your car and **drop the pieces into the collections under LOD0**: **Body**, **Wheel**, **Rear Wheel**,
   **Steering**, **Mudflaps**. As many pieces as you
   want, modifiers and all. The car faces **-Y** (Front view), with the ground at **Z = 0**.
3. Pieces that draw in a special way (glass, chrome, lamps...) get a name suffix, e.g. `Windows_Glass`, `Trim_Chrome`,
   `Lamp_TailOn`.
4. Give the materials image textures (PNG, TGA or BMP).
5. **Export this car...** into the game's `data/cars/day/<code>`. The day, night and eve cars all get written for you.
6. For the garage / dealer, hit **Menu car**, model or copy the car into it, and export it into `data/menu/cars/<code>`.

That's basically it. The rest of this file is the details.

## 3. Rules that apply to everything

| Rule | Why |
|---|---|
| **One car = one file.** The race car and the menu car are separate cars in Blender and separate files in the game. | That's how PD's disc does it: `cars/day`, `cars/night`, `cars/eve`, `menu/cars`. |
| **The save folder decides what gets built.** `data/cars/day` builds the race car. `data/menu/cars` builds the menu car. | A race car saved into `menu/cars` is built from its LOD0. A menu car saved into `cars/day` becomes a race car. |
| **Axes:** game (x, y, z) = Blender (-x, z, y). The car faces -Y, the ground is Z = 0, and +X in Blender is the car's **left**. | Cameras, lights and wheels get converted for you. |
| **The game skips back faces.** Every open surface has to face the viewer. Closed meshes need outward normals. | Turn on *Viewport Overlays > Face Orientation* before exporting: anything red is invisible in game. Use the `_2S` flag for parts that really are two-sided. |
| **Hiding an object leaves it out** (its eye icon). Hiding a whole **collection** (its eye in the Outliner) doesn't remove anything. | The importer hides LOD1-3 and the menu car with the collection eye so they don't sit on top of LOD0. Click the eye to see them; they export either way. |
| **Modifiers are applied on export** (Mirror, Subdivision, Bevel...). No need to apply anything by hand. | Animated parts are the exception: no modifiers that add or remove vertices on those. |
| **Textures: PNG, TGA or BMP.** | JPG and other lossy formats get refused with a message. |

## 4. Cars and files: new, import, export

### New car (sidebar > New car)
- **Race car** makes the collection `GT3 Car: <code>` with **LOD0-LOD3** sub-collections. LOD0 has the collections
  you drop parts into: **Body, Wheel, Rear Wheel, Steering, Mudflaps**.
- **Menu car** makes `GT3 Menu Car: <code>`, a single level (the garage never switches LOD), with Body, Wheel,
  Rear Wheel, Steering, Mudflaps, **Brake Front** and **Brake Rear**.
- Both come with a **Car Info** template, built from the median positions of PD's own cars:
  - 4 headlights, 4 brake lights, 5 brake flares (including the rear-window third light)
  - 2 exhausts and 20 cameras
  - hidden extras you unhide when your car has them: logo glow, rear fog, roof light, 2 spark points

### Import a PD car (sidebar > Import a PD car..., or File > Import > GT3 Car (editable))
Pick `data/cars/day/<code>`. The menu car comes from the same data folder. The parts show up **sorted into the same
collections a new car has** (Body, Wheel, Rear Wheel, Steering, Mudflaps, Brake Front/Rear, RT Shadow, Ground Shadow,
Headlight Beam), so you can tell what's what without reading a single name. You get:
- **GT3 Car: `<code>`**: the race car with LOD0-3, its wheel, shadows, lamps, animated parts, car info and colour list.
- **GT3 Menu Car: `<code>`**: hidden, because it overlaps the race car.

**Everything is stored in the .blend.** The decompile runs in a temporary folder that cleans itself up. Every texture
is packed, and the tyre and car-info files are embedded. Nothing gets left behind in Documents or %TEMP%. If you want
the decompiled PNGs and `car.json` on disk too, set **Also keep the files in**.

Blends made with older builds hid those collections with "Disable in Viewports", which the Outliner has no toggle for.
If yours is one of them, the sidebar offers **Make Hidden Collections Toggleable** (one click and it's sorted).

An imported car exports back **identical to PD, draw for draw**, including the night car, the animated parts and the
paint colours. Your edits carry over on their own: PD's reflection copies and the night car follow the parts they
belong to.

### Export (sidebar > Export this car..., or File > Export > GT3 Car)
| Saved into | Written | Notes |
|---|---|---|
| `data/cars/day` (or `night`, `eve`) | `data/cars/day/<code>`, `data/cars/night/<code>`, `data/cars/eve/<code>` | The race car's stock wheel lives **inside** the car file; the game loads no wheel file for it. Eve = the night file, same as PD's disc (282 of 287 cars). |
| `data/menu/cars` | `data/menu/cars/<code>` + `data/menu/wheel/<code>` | The showroom loads the wheel file by car code. |
| anywhere else | the file you named (the day car) | A note reminds you to export into the game's folder to get night, eve and the menu wheel. |

Saving into a game data folder also:
- writes the car's **colour list** into `data/database/carcolor.db` / `.sdb` 
  The first write keeps `.bak` copies.
- adds any **own brake art** to `data/race/brake.bin` The first write keeps a `.bak` copy.

Size limits (checked on every export): race car **688,128 bytes** and menu car **819,200 bytes** (the engine's
buffers). Each LOD's texture set can be **1,024 GS blocks** at most (256 KB; PD's biggest is 1,012).

**Options:**
- **Fill missing LODs:** an empty LOD1/LOD2 reuses the level above it.
- **Update the game's colour list:** on by default. When it's off, the car gets padded to the game's colour count
  instead.

## 5. Parts: collections (or names) decide what they are

**The easy way: collections.** Everything inside a collection takes that collection's meaning, at any depth, whatever
the objects are called. A car made of 40 pieces with modifiers goes into **Body** just as it is.

| Collection | What its objects become |
|---|---|
| **Body** | Car parts (opaque, lit). Use the name suffixes below for glass, chrome, lamps... |
| **Wheel** | The wheel: spokes + the backing disc |
| **Rear Wheel** | A different rear wheel (optional) |
| **Steering** | The steering wheel: follows the steering |
| **Mudflaps** | Mudflaps: **each flap's corner comes from where it sits** (front-left, front-right, rear-left, rear-right) |
| **Brake Front** / **Brake Rear** | The showroom's brake discs (menu car) |
| **RT Shadow** / **Ground Shadow** / **Headlight Beam** | Your own shadows / the headlight pool (optional: generated if missing) |
| **Glass**, **Chrome**, **Cutout**, **Soft**, **Glow**, **Tail Off**, **Tail On** ... | Any word from the tables below works as a collection name too |

Any of these can also sit inside LOD1 / LOD2 for the lower detail levels. Blender's `.001` endings are ignored.

**Names still work, and they win.** An object's own suffix beats its collection: `Windows_Glass` inside **Body** is
glass; `Hub_Rim` inside **Body** is the wheel. *Selected part* shows what an object turned into and which collection
decided it.

### What a part IS (role)
| Name word | What it becomes |
|---|---|
| *(none)* / `_Part` | A normal opaque, lit car part. |
| `_Rim` | **The wheel**. Default kind: cutout + reflection. |
| `_RearWheel` | A different rear wheel (optional). |
| `_BrakeFront` / `_BrakeRear` (or `_RimFront` / `_RimRear`) | The showroom's brake discs behind the rims (**menu car only**). |
| `_RTShadow` | The real-time shadow mesh. |
| `_GroundShadow` | The soft ground shadow. |
| `_HeadlightBeam` | The headlight pool on the road at night (additive, white on black). |

### How a car part DRAWS (style)
| Name word | Kind | Reflection | Emitted as |
|---|---|---|---|
| `_MainBody` | opaque | yes, strength = texture alpha | Paint / body shell. An EXT reflection copy is added right after the draw. |
| `_Chrome` | opaque | double strength (255) | Chrome trim. |
| `_Glass` | soft + depth | yes + glass stamp | PD's glass recipe: a soft blend that still writes depth, an alpha stamp, and a reflection. **No texture needed**: an untextured glass / soft part gets an 8x8 texture from its material's **Base Color + Alpha** (PD's glass is always textured, 331 of 331). Clear headlight cover = light Base Color, low Alpha. |
| `_Soft` | soft | no | Soft transparency (sun strips, soft decals). |
| `_Cutout` | cutout | no | Alpha-tested holes (grilles, meshes). Texture alpha 0 = hole. |
| `_Reflect` | EXT only | – | A **custom reflection shape**: drawn only as the reflection pass, untextured (it reflects the sky, like 754 of 759 of PD's reflection draws). E.g. mirror glass, or a reflection over a headlight. |
| `_Glow` | additive | no | Lamp glows. |

### Lamps and flags
| Name word | Effect |
|---|---|
| `_TailOff` / `_TailOn` | Drawn while the brake lamps are **off** / **on**. Put both on the same lamp. |
| `_TailOffNight` | Night car only, while the brakes are off: running lights. |
| `_2S` | Double sided. |
| `_Unlit` | Pre-lit: uses the vertex colours (colour attribute `GT3Color`), with no runtime lighting. |
| `_NoReflect` | No reflection copy. |
| `_Mudflap` (corner from position), `_MudflapFL/FR/RL/RR`, `_Flutter1..4`, `_Steering` | An animated part. |

Not sure what a name turned into? *Sidebar > Selected part* tells you.

## 6. Selected part: per-object overrides

Any of these override the name (the importer sets them to PD's exact values):

| Setting | Values |
|---|---|
| **Kind** | Auto, **Opaque** (alpha test > 32; alpha = reflection strength), **Cutout** (alpha > 0), **Cutout + blend** (alpha > 127, anti-aliased edges: PD rims), **No alpha test** (solid), **Soft** (blend, RGB only, no depth write), **Soft + depth** (PD glass), **Blend** (all channels), **Mask stamp** (alpha channel only), **Depth only**, **Depth only (alpha > 0)**, **EXT reflection**, **EXT reflection (masked)**, **Glint** (lit additive sheen), **Additive**, **Soft shadow** |
| **Reflection** | Auto / None / Reflect / Reflect (masked) |
| **Tail lamp** | Auto / None / Off / On / Off (night): the night car's running lights |
| **Lit** | Off = pre-lit vertex colour |
| **Double sided**, **Glass stamp**, **Reflection strength** (EXT vertex grey: 128 normal, 255 double, 64 half), **Tint 0.8** (PD tints 5,462 of 5,835 reflections by 0.8), **Draw order** (-1 = automatic) | |
| **Animation** | Auto, None, Flutter 1-4, Steering, Fixed |

## 7. Materials and textures

Every material with an **Image Texture** node becomes one GS texture. In *Selected part* > material:

| Setting | Values | Emitted as |
|---|---|---|
| **Format** | PSMT4 (16 colours, half the VRAM) / PSMT8 (256 colours) | The image is quantized exactly the way PD's tools did it (Wu quantizer). An image that already fits the palette stays exact. |
| **Wrap** | **Region clamp** (PD's default), **Repeat** (UVs past 0-1 tile), **Clamp**, **Region (pad, no stretch)** | *Region (pad)*: the picture keeps its own size inside the game's power-of-two buffer, so nothing gets stretched (e.g. a 60x30 number plate). Map your UVs onto the picture as you see it; the export fits them into the buffer. PD's cars use this on 375 textures and import the same way. |
| **Preset** | Plain, Paint, Chrome, Emissive (lamps), None (EXT / pre-lit) | The material's lighting values. |
| **is car paint** | on/off | The paint the colour list recolours. |
| **Night image** | an image | This texture on the **night and eve** car. |

**Texture files:**
- **PNG**: every PNG kind, including 16-bit and palette.
- **TGA**: true-colour, grey or colour-mapped; raw or RLE; 16/24/32-bit.
- **BMP**: 1/4/8-bit palette, RLE, 16/24/32-bit, bitfields.
- Packed and painted images work too.

The decoding matches the original tool's byte for byte. Non-power-of-two images get resampled (bicubic) to a power of
two, and images over 1,024 px get scaled down.

## 8. Wheels

- **Model the wheel where it sits** on the car (any wheel, any size, either side) and put it in the **Wheel**
  collection (or name it `..._Rim`). The export moves it to the origin and scales it to **PD's unit wheel** (radius 1,
  a left wheel). The game places and scales it on all four corners itself.
- **`_RearWheel`**: a different rear wheel (PD does this on 15 cars: wider rears, and Formula cars 2-4x). Without one,
  `_Rim` goes on all four corners.
- **The tyre's SHAPE is the game's; its texture is yours**
- **Spokes with holes need a backing disc.** Every single PD race rim (all 303) has an **opaque disc facing outwards
  behind the spokes**: radius about 1, just inboard of the face, kind **No alpha test**. Skip it and the holes show
  straight through the wheel, so it looks hollow or inside-out depending on the angle. Add it as a second object in
  **Wheel** and set its Kind to *No alpha test* (wheel parts are Cutout by default). **The export warns you** when a
  race rim has see-through spokes and no disc behind them.
- **Spinning face (race cars).** 279 of PD's 285 race rims draw that backing disc as a *spinning face* (texture slot
  511 + external texture 1). Every frame the game textures it from the rim's texture **sets 1 and 2**:
  - parked: set 1's still picture;
  - slow: set 1 turned a few times into a picture;
  - more than 3 degrees a frame: set 2, a pre-blurred picture.

  PD keeps **3 distance levels x 2 wheel models = 6 variations**. Heads up: the engine reads those sets without
  checking they exist. Earlier test builds kept the face but wrote no sets 1/2, so the face showed whatever the last
  wheel drawn had left behind: garbage colours, lag, and no brakes (the brakes show through the face).
  - Now an **import** gives the face its real picture (shared with the rim's texture when the pixels match) and keeps
    PD's sets in the .blend. The export puts them back untouched as long as the face picture is unchanged, and makes
    new ones from it otherwise (the still at up to 128x128 PSMT4, a 90-degree rotational blur at half size, then 1/2
    and 1/4 levels, PD's layout).
  - For **your own** wheel: *Selected part* > **Make spinning face (PD blur)** on the backing disc. Give it a centred,
    straight-on picture of the wheel.
  - A face without a picture exports as a plain part, with a warning.
- The rim's face has to point **outwards**. The export warns about face triangles that point into the car. No PD rim
  does that.
- Showroom wheels (the menu car's `_Rim`) are full 3D models. PD's rarely use a backing disc, so no warning there.
- LODs: the rim follows the car's LOD collections. An empty level reuses the one above it.

## 9. Brakes

The brake disc and caliper you see through the spokes come from **`data/race/brake.bin`**, a file **every car
shares**. A car just picks numbers from it (its car info holds one set for the front and one for the rear).

**Race car vs menu car:** the RACE car has no brake meshes at all - the game draws each brake as a flat disc + caliper
picture. The MENU car's brake discs are real meshes (collections *Brake Front* / *Brake Rear*). If you put a textured
mesh in a race car's *Brake Front* / *Brake Rear*, it won't be drawn: its TEXTURE becomes that axle's disc picture
('caliper' in its name = the caliper picture). Handy if you already made a disc for the menu car.

*Sidebar > Brakes & tyres > **Edit Brakes*** shows the two brakes (taken from the imported car or the template):

| Field | Meaning (read from the engine code) |
|---|---|
| **Disc** | The disc texture. The number **is** the texture: PD's cars use 1-6, and 1 is the plain disc. |
| **Caliper** | The caliper texture. **1 = no caliper** (the game skips it). PD uses 2-31. |
| **Size / Offset / Angle** | How big the brake quads are, how far from the centre, and the caliper's clock position (degrees). |
| **Own disc** | Your disc art (64x64, 256 colours; other sizes get resampled). |
| **Own caliper** | Your caliper art (32x32, 16 colours). |

**Own art:** export into the game's data folder and your images get **added** to `data/race/brake.bin`:
- New discs go in before the calipers, and new calipers go at the end.
- Every existing texture stays byte-identical, and every other car still points at the same art.
- The Disc / Caliper numbers are then set for you.
- Exporting again reuses the same entries. A small `brake.bin.gt3carmaker.json` next to the file remembers which image
  became which number.

The shared file does grow (a caliper costs about 3 GS blocks, a disc about 20), so keep an eye on other cars' brakes
the first time you test.

## 9b. Tyres

The game builds the tyre's shape itself; the car only carries its **picture**. Every PD tyre is ONE **128x64**
picture: the **top half is the tread**, the **bottom half is the sidewall** (where the brand lettering sits).

- *Sidebar > Brakes & tyres > Tyres*: pick a **Tyre picture** (any size gets fitted to 128x64, 16 colours).
- **Get a Tyre Picture**: loads this car's own tyre (an imported car) or any game car's tyre (type its code) as an
  editable picture - then just paint over it in Blender's Image Editor.
- Left empty: an imported car keeps its own tyre, and a new car gets a plain tyre in PD's layout.

## 10. Lamps, night and eve

- **Tail lamps:** model the lamp twice, `Lamp_TailOff` (dark) and `Lamp_TailOn` (lit). The game swaps them when you
  brake.
- **Night car:** give a material a **Night image**, which is the same art with the lamps lit: running lights, glowing
  headlamps. The export builds the **night car** with those textures swapped in. The **eve** (dusk) file is the night
  file, same as PD's disc. PD's night cars use the same shapes with lit lamp textures too.
- **Tail lamp: Off (night)** (Selected part > Tail lamp): a part drawn only on the night car while the brakes are off,
  e.g. running lights.
- **Night glows and brake flares** are car-info points: `Headlight_n` glows at night,
  `BrakeLight_n` lights up when braking, and `BrakeFlare_n` is the haze flare that flashes when braking.
- **`_HeadlightBeam`**: the pool of light on the road in front of the car at night (additive, white on black).

## 11. Shadows

| Object | Emitted as |
|---|---|
| `_RTShadow` | The real-time shadow: a **closed** mesh with **outward** normals (a hull around the car's footprint). |
| `_GroundShadow` | The baked soft shadow on the ground. Uses the vertex colour's **alpha** for softness (PD: about 0.6 in the middle, 0 at the edge). |
| *(none)* | Generated for you. Real-time hull = PD's 16-column skirt. Ground shadow = rings of 32/22/18 with the edge fading from alpha 77 to 0. |

The menu car only uses a ground shadow, and that one gets generated too if it's missing.

## 12. Animated parts

Put the part in the **Steering** or **Mudflaps** collection. Mudflaps need no names: each flap's corner (front-left,
front-right, rear-left, rear-right) comes from where it sits.

**How many poses:**
- **Mudflap:** the mesh (hanging straight) + **one** shape key (swept back). That's PD's recipe.
- **Steering wheel:** the mesh + **two or more** keys, an odd total with straight-ahead in the middle. The **mesh is
  full LEFT lock** (turned anticlockwise as the driver sees it), the middle key is straight ahead, and the last key is
  full RIGHT lock.

GT3 animates a part by sliding it along a **chain of poses** (engine opcode 53). You model the poses as shape keys:
select the part, then *Selected part* > **Add next key pose**. That makes `GT3 Morph`, then `GT3 Morph 2`, and so on.
The engine plays **mesh > GT3 Morph > GT3 Morph 2 > ...** as the driver goes from 0 to 1. At rest the driver sits in
the **middle** of the chain.

| Driver (name word) | What moves it (decoded from the engine) | PD's recipe |
|---|---|---|
| **Mudflaps** collection (or `_MudflapFL` / `FR` / `RL` / `RR`, `_Flutter1..4`) | A spring per wheel. It swings with the car's **forward/back acceleration** (throttle, brake, gear changes), gets random kicks that grow with **that wheel's speed** (so every corner moves a bit differently), and stops dead at both ends of the chain. A parked car gives it no input. | **2 poses:** the mesh **hanging straight**, one key **swept back and curled** (bent more towards the tip, not hinged; 3-5 rows of vertices). It rests half-way. Front flaps: tip about 8.5 cm back. Rear: about 17 cm. |
| **Steering** collection (or `_Steering`) | The steering: 0 = full left lock, 0.5 = straight, 1 = full right lock (read from the engine: 0.5 - 0.5 x steer / the car's max steer). | An **odd** number of poses with straight-ahead in the middle: left / centre / right (PD's cars), or 5 poses from full left to full right. |
| *Animation = Fixed* + a ratio | A constant pose along the chain. | e.g. a wing raised to 75%. |

Limits:
- Modifiers that add or remove vertices have to be applied first.
- Animated parts get no reflection copy, because the copy wouldn't move with them.
- Poses blend in straight lines, so a big rotation cuts across the arc and shrinks mid-way. For big turns, add more
  keys.

## 13. Paint colours

*Sidebar > Paint colours*: the list **is** what the dealer offers.

- **Row 0** = your textures as they are. Click a row to see that colour on the car.
- Each row has a **chip** (the dealer swatch), a **name** and a **finish**:
  - **Solid**, **Metallic**, **Pearl**, **Satin**, **Gloss**: PD's most used values over 177 cars.
  - **PD values**: an imported car's own values.
  - **Material's own**: each material keeps its own.
- A name of **`-`** shows **no name** in the dealer.
- Tick **is car paint** on the body material. **Make colour textures from the chips** then recolours the paint for every
  row, keeping the shading and leaving stripes and decals alone. You can also pick your own image per row
  (Selected part > material > the colour's texture). The generated textures get packed into the .blend.
- **Copy a game car's list...** takes the names and chips of the car you're replacing.

Export writes the colours **into the car** (palette patch sets + per-colour material tables) **and** into the game's
list, `data/database/carcolor.db` / `carcolor.sdb`, so the two always match. The dealer reads the names from
`carcolor.sdb`: languages 1-6 show the Name, the others NameJp. No list = the car keeps the game's colour count, and
all of them show your own paint.

## 14. LODs

- **Race car:** LOD0 (full detail), LOD1, LOD2, and LOD3, which is empty just like on PD's cars (very far away =
  nothing).
- **Empty LOD1/LOD2:** the export **reuses** the level above it. The draw is shared, not copied, so the file stays
  small. Rims and shadows share the same way.
- Each LOD gets its own texture set.
- Imported PD cars keep PD's own empty levels empty.
- The menu car has one level.
- Taking a tool-made car apart and building it again gives the **same size**: shared levels get recognised.

## 15. Car info

Empties in the car's **Car Info** collection, recognised **by name**:

| Name | What it is |
|---|---|
| `Headlight_0..` | Every light that glows at night: headlights, fogs, logos, roof lights. |
| `BrakeLight_0..` | Brake lights. |
| `BrakeFlare_0..` | The haze flares when braking (PD: 5, including the rear-window light). |
| `Exhaust_0..` | Exhaust outlets. |
| `Spark_0..` | Where sparks fly when the floor scrapes (22 of 285 PD cars). |
| `Cam_<SLOT>` | The 20 cameras: DEFAULT, CHASE, UNK_2, MIRROR_L/R, NOSE, BONNET, ROOF, BACK, TAIL, SIDE_L/R, FENDER_L/R, WHEEL_FL/FR/RL/RR, CAM18, CAM19. Position = the empty. Direction = its arrow. |

- *Car info* > **+** adds the next point of a kind at the 3D cursor.
- **Hidden points aren't exported.** That's how the template's extras stay off until you unhide them.
- **Which way a light faces matters:** the flare only shows from the side it faces. Select a light and *Selected part*
  shows "Glare shows from the FRONT / REAR of the car", with **Face front** / **Face rear** buttons. PD's rear fog
  lights face the rear (seen from behind); its roof lights and all front lights face the front. The export warns you
  when a light at the front faces the rear (or the other way round).
- The Cameras collection can be hidden or disabled. Cameras still get exported at their real positions.

## 16. Warnings the export gives you

| Warning | Meaning |
|---|---|
| **rim: its see-through spokes have NO opaque disc behind them** | Add a backing disc. |
| **face triangles point INTO the car** | Flip the rim's face. |
| **rim face '...' has no picture: drawn as a plain part** | A spinning face without a texture (for example an old .blend imported with an earlier test build). Re-import the car, or give the face a picture. |
| **OVER THE ENGINE CAP** | The car file is too big for the game. Lower the texture sizes or polygon counts. |
| **textures take N GS blocks** | One LOD's textures don't fit in 1,024 blocks. |
| **not a PNG, TGA or BMP** | Save the texture as one of those. |
| **the game lists N colours, the car has M** | Add a colour list, or let the export update the game's list. |
| **own brake art needs the export to go into the game's data folder** | It writes `data/race/brake.bin`. |
| **N hidden objects left out** | They're hidden (eye icon). |
| **Headlight_N: at the front but facing REAR** | Its glare only shows from behind. Select it: *Selected part* > **Face front**. |

## 17. Command line

The same engine runs outside Blender too (Python 3.10+ with numpy): `python -m gt3_car_maker <command>`

| Command | Does |
|---|---|
| `gt3-car-decompile <root> <code> <outDir>` / `<car file> <outDir>` | A car -> `car.json` + PNG textures. |
| `gt3-car-build <car.json> <outDir> [code]` | `car.json` -> the car files. |
| `gt3-carcolor <database dir> [code] [--json out]` | The game's colour list. |
| `gt3-carcolor-set <database dir> <code> <colours.json> [--out dir]` | Write a car's colour list. |
| `gt3-brake <brake.bin> [--png dir]` | List the shared brakes with the number a car uses for each, optionally as PNGs. |
| `gt3-brake-add <brake.bin> [--disc img]... [--caliper img]... [--like N] [--out file]` | Add own brake art. |

## 18. What the tool does not do

- **Tyre shape / size:** the game builds the tyre itself (the texture is yours tho)
- **New car codes, dealer lists, car names and specs:** those live in the game's menu (PMB) and parameter files. The
  tool replaces an existing car code.
- **`data/wheel/<code>`:** PD ships 464 of these (184 for codes that aren't even cars). They aren't the race or
  showroom wheel, and what the game loads them for is still unknown, so the tool leaves them alone.
- **JPG** and other lossy texture formats.

## 19. Credits and licence

Nenkai's research is the core of this human & machine made tool. Huge thanks to him: his PDTools is what this whole tool grew from.

MIT licence, for this add-on and for Nenkai's PDTools. See LICENSE.
The bicubic resize and Wu colour quantizer (`gt3car/imagesharp.py`) are Python ports of SixLabors.ImageSharp 3.1.6's
(Copyright (c) Six Labors), used under the Apache License, Version 2.0 (full text in LICENSE), made to reproduce the
original tool's output byte for byte.
