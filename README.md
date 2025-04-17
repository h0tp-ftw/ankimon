# Ankimon EXPERIMENTAL Branch

This branch contains the latest bugfixes and feature additions from community contributors! EXPERIMENTAL, **it can be very unstable**. So you get the coolest features but you have to deal with the issues as well!

### NOTE - for any contributions, see [How to contribute](https://github.com/h0tp-ftw/ankimon?tab=readme-ov-file#how-to-contribute-for-current-contributors) below

## Current contributors and maintainers in this repo

<table>
  <tr>
    <td align="center">
      <a href="https://github.com/h0tp-ftw">
        <img src="https://github.com/h0tp-ftw.png" width="100px;" alt="h0tp-ftw"/><br />
        <b>h0tp (owner)</b><br />
        <a href="https://github.com/h0tp-ftw">@h0tp-ftw</a>
      </a>
    </td>
    <td align="center">
      <a href="https://github.com/thepeacemonk">
        <img src="https://github.com/thepeacemonk.png" width="100px;" alt="thepeacemonk"/><br />
        <b>Peace</b><br />
        <a href="https://github.com/thepeacemonk">@thepeacemonk</a>
      </a>
    </td>
    <td align="center">
      <a href="https://github.com/gykoh">
        <img src="https://github.com/gykoh.png" width="100px;" alt="gykoh"/><br />
        <b>Gill</b><br />
        <a href="https://github.com/gykoh">@gykoh</a>
      </a>
    </td>
    <td align="center">
      <a href="https://github.com/richy431">
        <img src="https://github.com/richy431.png" width="100px;" alt="richy431"/><br />
        <b>richy</b><br />
        <a href="https://github.com/richy431">@richy431</a>
      </a>
    </td>
  </tr>
</table>

If you would like to join, please DM me on Discord (@h0tp)

## How to install this branch
- Make sure Ankimon is already installed in Anki!
- Backup your `mypokemon.json`, `mainpokemon.json`, `items.json` and `badges.json` files.
- Download this as ZIP (top right: Code -> Download ZIP)
- Extract ZIP, and put all contents of the `src/Ankimon` folder into your Ankimon addon folder.
- Restore your `mypokemon.json`, `mainpokemon.json`, `items.json` and `badges.json` files.
- Restart Anki

Recommended to install this on a separate installation of Anki, e.g. in a VM or different device.   

## Reporting bugs
- BEST way is to report on Ankimon Discord: https://discord.gg/eY8jPHZw4z
- You can add issues through GitHub: https://github.com/h0tp-ftw/ankimon/issues

## How to contribute for current contributors
- For each fix or feature you want, you need to have an INDIVIDUAL branch which is based on the main branch. So if you have two different bugfixes for example, make two branches.
- Name them in the format `fix/name-of-fix` or `feature/name-of-feature`.
- Place all your edits in that branch. Please do not change files on the `main` branch
- After your changes are ready, please put a pull request from your branch onto the main branch, so that we can test it out!

## How to contribute for new contributors
- The rule is that you must make at least ONE contribution to get access to this repo.
- Please fork this repo and make your code changes, then put a pull request to merge it to this repo!
- If you're not sure how to do this, read below on how to start, and ask on the Ankimon Discord server for help. My DMs are also open.

## I have NO coding experience but I want to start!
Coding experience is not needed! What's important is a PASSION to helping Ankimon development, and spending some time to learn! Ankimon is 100% volunteer-run, and community support is necessary to continue making it better :)

Keep in mind that a significant amount of Ankimon code is made via _vibe-coding_, i.e. letting AI write a lot of the code (and you make tweaks here and there). It is also mostly Python (for functions) and JavaScript (for data storage). 
- Skim through the [W3schools python tutorial](https://www.w3schools.com/python/python_syntax.asp). No need to learn every single thing, but have a general idea.
- Make some simple code for practice. Lots of things you can do for fun! Maybe try to add a new feature to Ankimon? Feel free to use your preferred AI chatbot to get coding help.
- Start going through the Ankimon code, especially [here](https://github.com/Unlucky-Life/ankimon/tree/main/src/Ankimon/functions) and learn about how these functions work. For example, if it says: 
```
catchable = set()
        for pokemon in self.excluded:
            if self.can_catch(caught_pokemon, pokemon):
                catchable.add(pokemon)
        return catchable
```
Can you figure out what this code is actually trying to do? 
- Any doubts on any step, ask ChatGPT or Perplexity AI your questions.
### After your first contribution ([section above](https://github.com/h0tp-ftw/ankimon?tab=readme-ov-file#how-to-contribute-for-new-contributors)), you can get access to our contributors channel and work as a team!  

## Start your Pokémon journey with Ankimon and make learning an adventure!
[![ko-fi](https://github.com/user-attachments/assets/1e5b9f0e-18c4-4115-a73e-08fc2e97f4d8)](https://ko-fi.com/A0A7SGLI8)




