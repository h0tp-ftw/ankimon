# Trading Pokémon

Last modified: `10. Dec. 2025`

Last Modifier: `Zuukiny` 

## Table Of Content

- [Trading Pokémon](#trading-pokémon)
    - [Table of Content](#table-of-content)
    - [Overview](#overview)
    - [Trade Code](#trade-code)
        - [Errors](#errors)
    - [Trade Password](#trade-password)
        - [Errors](#errors-1)
    - [Value Mappings](#value-mappings)

---

Ankimon allows users to trade Pokémon with others using a **password based system**. This trade is done between two individual Pokémon trainers by sharing their password code with the respective other. Since Ankimon shouldn't rely on online connectivity, the trading could be done by a single individual. To prevent this a `Hashing Function` is used to **encrypt individual Pokémon attributes** from both the receiving and sending Pokémon.

> 💡 This approach is especially practical for developers to test any changes regarding the trading functionality, since no second account has to be mocked.

> 🚨 Note: This also implies that 🦂 mean beans (cheaters) 🦂 may trade with themselves to gain an advantage against other players.  


## Overview

When trading, two steps are required for a successful trade. 

1. [Trade Code](#trade-code)
2. [Trade Password](#trade-password)

Each step involves both trading partners and their respective Pokémon that is up for trade.


## Trade Code

Every Pokémon generates a different Trade Code based on individual attributes. For example:
- EVs / IVs
- Moveset
- Shiny
- Pokémon_ID

---

Each code contains **at least 16 integer values** each separated by the **delimiter:** `,`

*Example*:

- `<value>, <value>, <value>, ...`

- `1, 0, 0, 0, 0, 0, 0, 28, 8,  ...`

A single player is required to enter the trade code of their trading partner to continue to step two. This means that during a regular trade (between two traders) both trade codes are necessary to continue the trade.

> 💡 This code contains all the information about the trader’s Pokémon (**attributes**).

> 🚨 Note: During this step a trader may change the code that was given to them by the trading partner. This allows `🦂 cheaters 🦂` to modify the Pokémon that they will receive after the trade, if they are familiar with the [attribute mappings](#value-mappings). Due to the sprite changing whenever the `Pokémon ID` part in the trade code is modified, this may lead traders to maliciously change code values since the GUI gives direct feedback to altered inputs.

### Errors

A list of every Error that users may encounter during this process is listed below.

| Identifier | Description | Error Message |
| ---------- | ----------- | ------------- |
| Code too short | If the input code contains less than 16 values | `Please enter a valid trade code from the other user.` |
| Same Pokémon_ID | If both traders exchange the same Pokémon (by ID) | `You cannot trade with a Pokémon of the same species (ID) as the one you're trading away!` |


## Trade Password

This password offers the necessary layer of protection to prevent `🦂 cheaters 🦂` from performing malicious trades.  

> 💡 This password is generated using a hashing function whose input is based on both trading codes. The function actually calculates both passwords, from which the trader only receives their password. The other part has to be entered by the trader themselves. 
> 
> Only if the password input matches the initial password generated for the trade partners Pokémon, will the trade be valid.

### Errors

A list of every Error that users may encounter during this process is listed below.

| Identifier | Description | Error Message |
| ---------- | ----------- | ------------- |
| No Input | If the input field is left empty | `Please enter the password part from the other user.` |
| Code too short | If the input code is less than 34 | `Incorrect password format.` |
| Different Versions | If both traders have different versions | `Trade incompatible due to Ankimon trade versions. Your version: <my_version>, partner's version: <their_version>. Please get the latest version of Ankimon for both users!` |
| Same Pokémon_ID | If both traders exchange the same Pokémon (by ID) | `You cannot trade with a Pokémon of the same species (ID) as the one you're trading away!` |
| Different password | If the password does not match the initially generated password (See: [here](#trade-password)) | `Incorrect password part. Please check with the other user.` |


## Value Mappings

The value mappings are processed by the `process_trade` method. It assigns the attributes to the newly received Pokémon through the Trade Codes `values`.

```python
# Each attribute is mapped to a specific value in the Trade Code.

def process_trade(self, numbers):
    pokemon_id = numbers[0]
    level = numbers[1]
    gender_id = numbers[2]
    shiny = numbers[3]
    ev_stats = dict(zip(['hp', 'atk', 'def', 'spa', 'spd', 'spe'], numbers[4:10]))
    iv_stats = dict(zip(['hp', 'atk', 'def', 'spa', 'spd', 'spe'], numbers[10:16]))
    attacks = [self.find_move_by_num(attack_id)['name'] for attack_id in numbers[16:]]
```
