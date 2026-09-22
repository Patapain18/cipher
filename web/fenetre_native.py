"""
Fenêtre native macOS pour une application web locale.

Module autonome, jumeau de celui du Hub. Il est volontairement DUPLIQUÉ
plutôt que partagé : chaque application doit pouvoir être empaquetée
seule, sans dépendre d'un dossier voisin qui n'existera pas dans le
bundle. Si tu le corriges ici, reporte la correction dans l'autre.

Ce qu'il apporte face à `webbrowser.open()` :
  — une fenêtre à soi, sans barre d'adresse ni onglets ;
  — une icône dans le Dock et un Cmd+Q qui fonctionne ;
  — aucune dépendance à un navigateur installé, WebKit faisant partie
    du système.
"""

import os
import subprocess
import time
import webbrowser


def ouvrir(url, titre, largeur=1280, hauteur=860, port_interne=None,
           a_la_fermeture=None):
    """
    Affiche `url` dans une fenêtre native et rend la main à la fermeture.

    `port_interne` distingue les liens qui appartiennent à l'application
    de ceux qui mènent ailleurs : ces derniers partent dans le
    navigateur au lieu de remplacer la page en cours.

    Trois recours en cascade, du meilleur au moins bon : fenêtre native,
    Chrome en mode application, navigateur par défaut.
    """
    try:
        _fenetre_webkit(url, titre, largeur, hauteur, port_interne, a_la_fermeture)
        return "native"
    except Exception:
        pass

    chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    if os.path.exists(chrome):
        subprocess.Popen([chrome, f"--app={url}"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _attendre_indefiniment()
        return "chrome"

    webbrowser.open(url)
    _attendre_indefiniment()
    return "navigateur"


def _attendre_indefiniment():
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


def _fenetre_webkit(url, titre, largeur, hauteur, port_interne, a_la_fermeture):
    import AppKit
    from AppKit import (NSApplication, NSWindow, NSObject, NSMenu, NSMenuItem,
                        NSBackingStoreBuffered, NSApplicationActivationPolicyRegular,
                        NSAppearance, NSColor)
    from Foundation import NSMakeRect, NSTimer, NSURL, NSURLRequest
    from WebKit import WKWebView, WKWebViewConfiguration

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)

    # Une barre de menus, uniquement pour que Cmd+Q fonctionne : sans
    # elle, macOS n'attache aucun raccourci système et l'application
    # reste dans le Dock sans moyen évident de l'arrêter.
    barre = NSMenu.alloc().init()
    entree = NSMenuItem.alloc().init()
    barre.addItem_(entree)
    app.setMainMenu_(barre)
    menu = NSMenu.alloc().init()
    menu.addItem_(NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        f"Quitter {titre}", "terminate:", "q"))
    entree.setSubmenu_(menu)

    # Un menu Édition, sinon Cmd+C / Cmd+V / Cmd+A ne font rien dans la
    # page : sous AppKit, ces raccourcis passent par les items de menu
    # et leurs sélecteurs standard — sans item, la WKWebView ne reçoit
    # jamais la frappe. Impossible, avant, de coller un prix d'achat.
    edition = NSMenuItem.alloc().init()
    barre.addItem_(edition)
    menu_edition = NSMenu.alloc().initWithTitle_("Édition")
    for item in (("Annuler", "undo:", "z"), ("Rétablir", "redo:", "Z"), None,
                 ("Couper", "cut:", "x"), ("Copier", "copy:", "c"),
                 ("Coller", "paste:", "v"), ("Tout sélectionner", "selectAll:", "a")):
        if item is None:
            menu_edition.addItem_(NSMenuItem.separatorItem())
        else:
            menu_edition.addItem_(
                NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(*item))
    edition.setSubmenu_(menu_edition)

    fenetre_menu = NSMenuItem.alloc().init()
    barre.addItem_(fenetre_menu)
    menu_fenetre = NSMenu.alloc().initWithTitle_("Fenêtre")
    menu_fenetre.addItem_(NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "Réduire", "performMiniaturize:", "m"))
    menu_fenetre.addItem_(NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "Fermer", "performClose:", "w"))
    fenetre_menu.setSubmenu_(menu_fenetre)

    style = (AppKit.NSWindowStyleMaskTitled
             | AppKit.NSWindowStyleMaskClosable
             | AppKit.NSWindowStyleMaskMiniaturizable
             | AppKit.NSWindowStyleMaskResizable)
    cadre = NSMakeRect(0, 0, largeur, hauteur)

    fenetre = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        cadre, style, NSBackingStoreBuffered, False)
    fenetre.setTitle_(titre)
    fenetre.setMinSize_((820, 560))
    # « Plein jour, jamais de nuit » est une décision de l'application :
    # elle vaut pour la fenêtre entière. Sans ça, un Mac en mode sombre
    # posait une barre de titre noire sur une page crème — la vue avait
    # l'air posée dans une autre app. Et le fond de la fenêtre est crème
    # dès la première image, avant même que la page ait chargé.
    fenetre.setAppearance_(NSAppearance.appearanceNamed_("NSAppearanceNameAqua"))
    fenetre.setBackgroundColor_(NSColor.colorWithSRGBRed_green_blue_alpha_(
        0.957, 0.945, 0.918, 1.0))
    # La fenêtre se souvient de sa taille et de sa position ; on ne
    # centre qu'au tout premier lancement.
    if not fenetre.setFrameUsingName_("principale"):
        fenetre.center()
    fenetre.setFrameAutosaveName_("principale")

    vue = WKWebView.alloc().initWithFrame_configuration_(
        cadre, WKWebViewConfiguration.alloc().init())
    vue.setAutoresizingMask_(2 | 16)      # suit la largeur et la hauteur

    # L'inspecteur web, sur demande explicite :
    #     FENETRE_INSPECTEUR=1 open -a "Signaux actions.app"
    # puis clic droit → « Inspecter l'élément ».
    #
    # C'est `setInspectable:` sur la WKWebView, et non
    # `setDeveloperExtrasEnabled:` sur les préférences — cette dernière
    # ne fait plus rien depuis macOS 13.3, ce qui donne l'impression
    # que l'inspecteur est cassé alors qu'il n'a jamais été activé.
    #
    # Éteint par défaut, et c'est voulu : une application qu'on
    # distribue ne doit pas exposer ses entrailles au premier clic
    # droit venu.
    if os.environ.get("FENETRE_INSPECTEUR") == "1" and hasattr(vue, "setInspectable_"):
        vue.setInspectable_(True)

    fenetre.setContentView_(vue)
    vue.loadRequest_(NSURLRequest.requestWithURL_(NSURL.URLWithString_(url)))

    # Cmd+R recharge la page : précieux quand on retouche style.css.
    presentation = NSMenuItem.alloc().init()
    barre.addItem_(presentation)
    menu_pres = NSMenu.alloc().initWithTitle_("Présentation")
    recharger = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "Recharger la page", "reload:", "r")
    recharger.setTarget_(vue)
    menu_pres.addItem_(recharger)
    presentation.setSubmenu_(menu_pres)

    # La page ne sait pas quand la fenêtre est réduite dans le Dock ou
    # cachée par une autre : `document.hidden` reste faux, et la vue
    # baie animerait sa ville pour personne. On le lui dit d'ici. La
    # fonction est facultative côté page : une page qui ne la définit
    # pas n'en souffre pas.
    def dire_visible(visible):
        vue.evaluateJavaScript_completionHandler_(
            "window.fenetreVisible && window.fenetreVisible(%s)"
            % ("true" if visible else "false"), None)

    class Delegue(NSObject):
        def windowDidMiniaturize_(self, notification):
            dire_visible(False)

        def windowDidDeminiaturize_(self, notification):
            dire_visible(True)

        def windowDidChangeOcclusionState_(self, notification):
            visible = bool(fenetre.occlusionState()
                           & AppKit.NSWindowOcclusionStateVisible)
            dire_visible(visible)

        def windowWillClose_(self, notification):
            if a_la_fermeture:
                try:
                    a_la_fermeture()
                except Exception:
                    pass
            AppKit.NSApp.terminate_(None)

        # Une WKWebView IGNORE `target="_blank"` tant que l'application
        # ne dit pas quoi en faire : WebKit réclame ici une nouvelle vue
        # et, sans réponse, le clic ne produit rien du tout.
        def webView_createWebViewWithConfiguration_forNavigationAction_windowFeatures_(
                self, v, configuration, action, options):
            u = action.request().URL()
            if u:
                AppKit.NSWorkspace.sharedWorkspace().openURL_(u)
            return None

        # Filet pour un lien SANS `_blank` : il remplacerait sinon
        # l'application par une page étrangère, dans sa propre fenêtre,
        # sans aucun moyen de revenir en arrière.
        def webView_decidePolicyForNavigationAction_decisionHandler_(
                self, v, action, decision):
            u = action.request().URL()
            interne = (port_interne is None
                       or (u and u.port() and int(u.port()) == port_interne))
            if action.navigationType() == 0 and not interne:   # 0 = clic
                if u:
                    AppKit.NSWorkspace.sharedWorkspace().openURL_(u)
                decision(0)                                    # annuler
                return
            decision(1)                                        # autoriser

    delegue = Delegue.alloc().init()
    fenetre.setDelegate_(delegue)
    vue.setUIDelegate_(delegue)
    vue.setNavigationDelegate_(delegue)
    # Références gardées côté Python : sans elles, le ramasse-miettes
    # emporterait le delegate et plus rien ne répondrait.
    fenetre.retain()
    delegue.retain()

    fenetre.makeKeyAndOrderFront_(None)
    app.activateIgnoringOtherApps_(True)

    # — Pourquoi on redemande la taille juste après —
    #
    #   Selon ce qui occupe l'écran au moment de l'ouverture (Stage
    #   Manager, une application en plein écran), macOS rabote parfois
    #   la fenêtre à l'affichage : demandée à 1440, elle apparaît à
    #   1291. Le phénomène est INTERMITTENT — mesuré une fois sur deux
    #   sur cette machine, ce qui est pire qu'un plafond franc : le
    #   tableau des 49 valeurs tient à partir de 1421 px, donc deux
    #   colonnes disparaissent un jour sur deux, sans raison visible.
    #
    #   On revient donc demander la place une fois la fenêtre posée, et
    #   seulement si elle a rétréci ET que l'écran peut l'accueillir.
    #   Un seul rappel, pas une boucle : si le système refuse deux fois,
    #   c'est qu'il a une raison, et on ne va pas lutter contre lui.
    def _reclamer_la_place(_timer=None):
        cadre = fenetre.frame()
        if cadre.size.width >= largeur - 1:
            return
        ecran = fenetre.screen()
        if not ecran or ecran.visibleFrame().size.width < largeur:
            return
        voulue = NSMakeRect(cadre.origin.x, cadre.origin.y,
                            largeur, cadre.size.height)
        fenetre.setFrame_display_animate_(voulue, True, False)
        fenetre.center()

    NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
        0.45, False, _reclamer_la_place)

    app.run()
