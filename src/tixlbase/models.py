from itertools import product
import copy
import uuid

from django.db import models
from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.utils.translation import ugettext_lazy as _
from django.template.defaultfilters import date as _date
from django.core.validators import RegexValidator
import six
from versions.models import Versionable as BaseVersionable
from versions.models import VersionedForeignKey, VersionedManyToManyField, get_utc_now

from tixlbase.types import VariationDict


class Versionable(BaseVersionable):

    class Meta:
        abstract = True

    def clone_shallow(self, forced_version_date=None):
        """
        This behaves like clone(), but misses all the Many2Many-relation-handling. This is
        a performance optimization for cases in which we have to handle the Many2Many relations
        by handy anyways.
        """
        if not self.pk:
            raise ValueError('Instance must be saved before it can be cloned')

        if self.version_end_date:
            raise ValueError('This is a historical item and can not be cloned.')

        if forced_version_date:
            if not self.version_start_date <= forced_version_date <= get_utc_now():
                raise ValueError('The clone date must be between the version start date and now.')
        else:
            forced_version_date = get_utc_now()

        earlier_version = self

        later_version = copy.copy(earlier_version)
        later_version.version_end_date = None
        later_version.version_start_date = forced_version_date

        # set earlier_version's ID to a new UUID so the clone (later_version) can
        # get the old one -- this allows 'head' to always have the original
        # id allowing us to get at all historic foreign key relationships
        earlier_version.id = six.u(str(uuid.uuid4()))
        earlier_version.version_end_date = forced_version_date
        earlier_version.save()

        later_version.save()

        return later_version


class UserManager(BaseUserManager):
    """
    This is the user manager for our custom user model. See the User
    model documentation to see what's so special about our user model.
    """

    def create_user(self, identifier, username, password=None):
        user = self.model(identifier=identifier)
        user.set_password(password)
        user.save()
        return user

    def create_superuser(self, identifier, username, password=None):
        if password is None:
            raise Exception("You must provide a password")
        user = self.model(identifier=identifier, username=username)
        user.is_staff = True
        user.is_superuser = True
        user.set_password(password)
        user.save()
        return user


class User(AbstractBaseUser, PermissionsMixin):
    """
    This is the user model used by tixl for authentication.
    Handling users is somehow complicated, as we try to have two
    classes of users in one system:
        (1) We want global users who can just login into tixl and
            buy tickets for multiple events -- we also need those
            global users for event organizers who should not need
            multiple users for managing multiple events.
        (2) We want local users who exist only in the scope of a
            certain event
    The hard part is to find a primary key to identify all of these
    users. Letting the users choose usernames is a bad idea, as
    the primary key needs to be unique and there is no reason for a
    local user to block a name for all time. Using e-mail addresses
    is not a good idea either, for two reasons: First, a user might
    have multiple local users (so they are not unique), and second,
    it should be possible to create anonymous users without having
    to supply an e-mail address.
    Therefore, we use an abstract "identifier" field as the primary
    key. The identifier is:
        (1) the e-mail address for global users. An e-mail address
            is and should be required for them and global users use
            their e-mail address for login.
        (2) "{username}@{event.id}.event.tixl" for local users, who
            use their username to login on the event page.
    The model's save() method automatically fills the identifier field
    according to this scheme when it is empty. The __str__() method
    returns the identifier.

    The is_staff field is only True for system operators.
    """

    USERNAME_FIELD = 'identifier'
    REQUIRED_FIELDS = ['username']

    identifier = models.CharField(max_length=255, unique=True)
    username = models.CharField(max_length=120, blank=True,
                                null=True,
                                help_text=_('Letters, digits and @/./+/-/_ only.'))
    event = models.ForeignKey('Event', related_name="users",
                              null=True, blank=True,
                              on_delete=models.PROTECT)
    email = models.EmailField(unique=False, db_index=True,
                              null=True, blank=True,
                              verbose_name=_('E-mail'))
    givenname = models.CharField(max_length=255, blank=True,
                                 null=True,
                                 verbose_name=_('Given name'))
    familyname = models.CharField(max_length=255, blank=True,
                                  null=True,
                                  verbose_name=_('Family name'))
    is_active = models.BooleanField(default=True,
                                    verbose_name=_('Is active'))
    is_staff = models.BooleanField(default=False,
                                   verbose_name=_('Is site admin'))
    date_joined = models.DateTimeField(auto_now_add=True,
                                       verbose_name=_('Date joined'))
    locale = models.CharField(max_length=50,
                              choices=settings.LANGUAGES,
                              default=settings.LANGUAGE_CODE,
                              verbose_name=_('Language'))
    timezone = models.CharField(max_length=100,
                                default=settings.TIME_ZONE,
                                verbose_name=_('Timezone'))

    objects = UserManager()

    class Meta:
        verbose_name = _("User")
        verbose_name_plural = _("Users")
        unique_together = (("event", "username"),)

    def __str__(self):
        return self.identifier

    def save(self, *args, **kwargs):
        if self.identifier is None:
            if self.event is None:
                self.identifier = self.email.lower()
            else:
                self.identifier = "%s@%d.event.tixl" % (self.username.lower(), self.event.id)
        if not self.pk:
            self.identifier = self.identifier.lower()
        super().save(*args, **kwargs)

    def get_short_name(self) -> str:
        if self.givenname:
            return self.givenname
        elif self.familyname:
            return self.familyname
        else:
            return self.username

    def get_full_name(self) -> str:
        if self.givenname and not self.familyname:
            return self.givenname
        elif not self.givenname and self.familyname:
            return self.familyname
        elif self.familyname and self.givenname:
            return '%(family)s, %(given)s' % {
                'family': self.familyname,
                'given': self.givenname
            }
        else:
            return self.username


class Organizer(Versionable):
    """
    This model represents an entity organizing events, like a company.
    Any organizer has a unique slug, which is a short name (alphanumeric,
    all lowercase) being used in URLs.
    """

    name = models.CharField(max_length=200,
                            verbose_name=_("Name"))
    slug = models.CharField(max_length=50,
                            unique=True, db_index=True,
                            verbose_name=_("Slug"))
    permitted = models.ManyToManyField(User, through='OrganizerPermission',
                                       related_name="organizers")

    class Meta:
        verbose_name = _("Organizer")
        verbose_name_plural = _("Organizers")
        ordering = ("name",)

    def __str__(self):
        return self.name


class OrganizerPermission(Versionable):
    """
    The relation between an Organizer and an User who has permissions to
    access an organizer profile.
    """

    organizer = VersionedForeignKey(Organizer)
    user = models.ForeignKey(User, related_name="organizer_perms")
    can_create_events = models.BooleanField(
        default=True,
        verbose_name=_("Can create events"),
    )

    class Meta:
        verbose_name = _("Organizer permission")
        verbose_name_plural = _("Organizer permissions")

    def __str__(self):
        return _("%(name)s on %(object)s") % {
            'name': str(self.user),
            'object': str(self.organizer),
        }


class Event(Versionable):
    """
    This model represents an event. An event is anything you can buy
    tickets for. It belongs to one orgnaizer and has a name and a slug,
    the latter being a short, alphanumeric, all-lowercase name being
    used in URLs. The slug has to be unique among the events of the same
    organizer.

    An event can hold several properties, such as a default locale and
    currency.

    The event has date_from and date_to field which mark the actual
    datetime of the event itself. The show_date_to and show_times
    fields are used to control the display of these dates. (Without
    show_times only days are shown, now times.)

    The presale_start and presale_end fields mark the time frame in
    which tickets are sold for this event. These two dates override
    every other restrictions to ticket sale if set.

    The payment_term_days field holds the number of days after
    submitting a ticket order, in which the ticket has to be paid.
    The payment_term_last is the day all orders must be paid by, no
    matter when they were ordered (and thus, ignoring payment_term_days).
    """

    organizer = VersionedForeignKey(Organizer, related_name="events",
                                    on_delete=models.PROTECT)
    name = models.CharField(max_length=200,
                            verbose_name=_("Name"))
    slug = models.CharField(
        max_length=50, db_index=True,
        help_text=_(
            "Should be short, only contain lowercase letters and numbers, and must be unique among your events. "
            + "This is being used in addresses and bank transfer references."),
        validators=[
            RegexValidator(
                regex="^[a-zA-Z0-9.-]+$",
                message=_("The slug may only contain letters, numbers, dots and dashes."),
            )
        ],
        verbose_name=_("Slug"),
    )
    permitted = models.ManyToManyField(User, through='EventPermission',
                                       related_name="events",)
    locale = models.CharField(max_length=10,
                              choices=settings.LANGUAGES,
                              verbose_name=_("Default locale"))
    timezone = models.CharField(max_length=100,
                                default=settings.TIME_ZONE,
                                verbose_name=_('Default timezone'))
    currency = models.CharField(max_length=10,
                                verbose_name=_("Default currency"))
    date_from = models.DateTimeField(verbose_name=_("Event start time"))
    date_to = models.DateTimeField(null=True, blank=True,
                                   verbose_name=_("Event end time"))
    show_date_to = models.BooleanField(
        default=True,
        verbose_name=_("Show event end date"),
        help_text=_("If disabled, only event's start date will be displayed to the public."),
    )
    show_times = models.BooleanField(
        default=True,
        verbose_name=_("Show dates with time"),
        help_text=_("If disabled, the event's start and end date will be displayed without the time of day."),
    )
    presale_end = models.DateTimeField(
        null=True, blank=True,
        verbose_name=_("End of presale"),
        help_text=_("No items will be sold after this date."),
    )
    presale_start = models.DateTimeField(
        null=True, blank=True,
        verbose_name=_("Start of presale"),
        help_text=_("No items will be sold before this date."),
    )
    payment_term_days = models.PositiveIntegerField(
        default=14,
        verbose_name=_("Payment term in days"),
        help_text=_("The number of days after placing an order the user has to pay to preserve his reservation."),
    )
    payment_term_last = models.DateTimeField(
        null=True, blank=True,
        verbose_name=_("Last date of payments"),
        help_text=_("The last date any payments are accepted. This has precedence over the number of days configured above.")
    )
    plugins = models.TextField(
        null=True, blank=True,
        verbose_name=_("Plugins"),
    )

    class Meta:
        verbose_name = _("Event")
        verbose_name_plural = _("Events")
        # unique_together = (("organizer", "slug"),)  # TODO: Enforce manually
        ordering = ("date_from", "name")

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        obj = super().save(*args, **kwargs)
        self.get_cache().clear()
        return obj

    def get_plugins(self) -> "list[str]":
        if self.plugins is None:
            return []
        return self.plugins.split(",")

    def get_date_from_display(self) -> str:
        return _date(
            self.date_from,
            "DATETIME_FORMAT" if self.show_times else "DATE_FORMAT"
        )

    def get_date_to_display(self) -> str:
        if not self.show_date_to:
            return ""
        return _date(
            self.date_to,
            "DATETIME_FORMAT" if self.show_times else "DATE_FORMAT"
        )

    def get_cache(self) -> "tixlbase.cache.EventRelatedCache":
        from tixlbase.cache import EventRelatedCache
        return EventRelatedCache(self)


class EventPermission(Versionable):
    """
    The relation between an Event and an User who has permissions to
    access an event.
    """

    event = VersionedForeignKey(Event)
    user = models.ForeignKey(User, related_name="event_perms")
    can_change_settings = models.BooleanField(
        default=True,
        verbose_name=_("Can change event settings")
    )
    can_change_items = models.BooleanField(
        default=True,
        verbose_name=_("Can change item settings")
    )

    class Meta:
        verbose_name = _("Event permission")
        verbose_name_plural = _("Event permissions")

    def __str__(self):
        return _("%(name)s on %(object)s") % {
            'name': str(self.user),
            'object': str(self.event),
        }


class ItemCategory(Versionable):
    """
    Items can be sorted into categories, which only have a name and a
    configurable order
    """
    event = VersionedForeignKey(
        Event,
        on_delete=models.CASCADE,
        related_name='categories',
    )
    name = models.CharField(
        max_length=255,
        verbose_name=_("Category name"),
    )
    position = models.IntegerField(
        default=0
    )

    class Meta:
        verbose_name = _("Item category")
        verbose_name_plural = _("Item categories")
        ordering = ('position',)

    def __str__(self):
        return self.name

    def delete(self, *args, **kwargs):
        super().delete(*args, **kwargs)
        if self.event:
            self.event.get_cache().clear()

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.event:
            self.event.get_cache().clear()


class Property(Versionable):
    """
    A property is a modifier which can be applied to an Item. For example
    'Size' would be a property associated with the item 'T-Shirt'.
    """

    event = VersionedForeignKey(
        Event,
        related_name="properties",
    )
    name = models.CharField(
        max_length=250,
        verbose_name=_("Property name"),
    )

    class Meta:
        verbose_name = _("Item property")
        verbose_name_plural = _("Item properties")

    def __str__(self):
        return self.name

    def delete(self, *args, **kwargs):
        super().delete(*args, **kwargs)
        if self.event:
            self.event.get_cache().clear()

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.event:
            self.event.get_cache().clear()


class PropertyValue(Versionable):
    """
    A value of a property. If the property would be 'T-Shirt size',
    this could be 'M' or 'L'
    """

    prop = VersionedForeignKey(
        Property,
        on_delete=models.CASCADE,
        related_name="values"
    )
    value = models.CharField(
        max_length=250,
        verbose_name=_("Value"),
    )
    position = models.IntegerField(
        default=0
    )

    class Meta:
        verbose_name = _("Property value")
        verbose_name_plural = _("Property values")
        ordering = ("position",)

    def __str__(self):
        return "%s: %s" % (self.prop.name, self.value)

    def delete(self, *args, **kwargs):
        super().delete(*args, **kwargs)
        if self.prop:
            self.prop.event.get_cache().clear()

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.prop:
            self.prop.event.get_cache().clear()


class Question(Versionable):
    """
    A question is an input field that can be used to extend a ticket
    by custom information, e.g. "Attendee name" or "Attendee age".
    """
    TYPE_NUMBER = "N"
    TYPE_STRING = "S"
    TYPE_TEXT = "T"
    TYPE_BOOLEAN = "B"
    TYPE_CHOICES = (
        (TYPE_NUMBER, _("Number")),
        (TYPE_STRING, _("Text (one line)")),
        (TYPE_TEXT, _("Multiline text")),
        (TYPE_BOOLEAN, _("Yes/No")),
    )

    event = VersionedForeignKey(
        Event,
        related_name="questions",
    )
    question = models.TextField(
        verbose_name=_("Question"),
    )
    type = models.CharField(
        max_length=5,
        choices=TYPE_CHOICES,
        verbose_name=_("Question type"),
    )
    required = models.BooleanField(
        default=False,
        verbose_name=_("Required question"),
    )

    class Meta:
        verbose_name = _("Question")
        verbose_name_plural = _("Questions")

    def __str__(self):
        return self.question

    def delete(self, *args, **kwargs):
        super().delete(*args, **kwargs)
        if self.event:
            self.event.get_cache().clear()

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.event:
            self.event.get_cache().clear()


class Item(Versionable):
    """
    An item is a thing which can be sold. It belongs to an
    event and may or may not belong to a category.

    It has a default price which might by overriden by
    restrictions.

    Items can not be deleted, as this would cause database
    inconsistencies. Instead, they have an attribute "deleted".
    Deleted items will not be shown anywhere.
    """
    event = VersionedForeignKey(
        Event,
        on_delete=models.PROTECT,
        related_name="items",
        verbose_name=_("Event"),
    )
    category = VersionedForeignKey(
        ItemCategory,
        on_delete=models.PROTECT,
        related_name="items",
        blank=True, null=True,
        verbose_name=_("Category"),
    )
    name = models.CharField(
        max_length=255,
        verbose_name=_("Item name")
    )
    active = models.BooleanField(
        default=True,
        verbose_name=_("Active"),
    )
    deleted = models.BooleanField(default=False)
    short_description = models.TextField(
        verbose_name=_("Short description"),
        help_text=_("This is shown below the item name in lists."),
        null=True, blank=True,
    )
    long_description = models.TextField(
        verbose_name=_("Long description"),
        null=True, blank=True,
    )
    default_price = models.DecimalField(
        null=True, blank=True,
        verbose_name=_("Default price"),
        max_digits=7, decimal_places=2
    )
    tax_rate = models.DecimalField(
        null=True, blank=True,
        verbose_name=_("Taxes included in percent"),
        max_digits=7, decimal_places=2
    )
    properties = VersionedManyToManyField(
        Property,
        related_name='items',
        verbose_name=_("Properties"),
        blank=True,
        help_text=_(
            'The selected properties will be available for the user '
            + 'to select. After saving this field, move to the '
            + '\'Variations\' tab to configure the details.'
        )
    )
    questions = VersionedManyToManyField(
        Question,
        related_name='items',
        verbose_name=_("Questions"),
        blank=True,
        help_text=_(
            'The user will be asked to fill in answers for the '
            + 'selected questions'
        )
    )

    class Meta:
        verbose_name = _("Item")
        verbose_name_plural = _("Items")

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.event:
            self.event.get_cache().clear()

    def delete(self):
        self.deleted = True
        self.active = False
        super().save()
        if self.event:
            self.event.get_cache().clear()

    def get_all_variations(self, use_cache: bool=False) -> "list[VariationDict]":
        """
        This method returns a list containing all variations of this
        item. The list contains one VariationDict per variation, where
        the Proprty IDs are keys and the PropertyValue objects are
        values. If an ItemVariation object exists, it is available in
        the dictionary via the special key 'variation'.

        VariationDicts differ from dicts only by specifying some extra
        methods.
        """
        if use_cache and hasattr(self, '_get_all_variations_cache'):
            return self._get_all_variations_cache

        all_variations = self.variations.all().prefetch_related("values")
        all_properties = self.properties.all().prefetch_related("values")
        variations_cache = {}
        for var in all_variations:
            key = []
            for v in var.values.all():
                key.append((v.prop_id, v.identity))
            key = tuple(sorted(key))
            variations_cache[key] = var

        result = []
        for comb in product(*[prop.values.all() for prop in all_properties]):
            if len(comb) == 0:
                result.append(VariationDict())
                continue
            key = []
            var = VariationDict()
            for v in comb:
                key.append((v.prop.identity, v.identity))
                var[v.prop.identity] = v
            key = tuple(sorted(key))
            if key in variations_cache:
                var['variation'] = variations_cache[key]
            result.append(var)

        self._get_all_variations_cache = result
        return result


class ItemVariation(Versionable):
    """
    A variation is an item combined with values for all properties
    associated with the item. For example, if your item is 'T-Shirt'
    and your properties are 'Size' and 'Color', then an example for a
    variation would be 'T-Shirt XL read'.

    Attention: _ALL_ combinations of PropertyValues _ALWAYS_ exist,
    even if there is no ItemVariation object for them! ItemVariation objects
    do NOT prove existance, they are only available to make it possible
    to override default values (like the price) for certain combinations
    of property values.

    They also allow to explicitly EXCLUDE certain combinations of property
    values by creating an ItemVariation object for them with active set to
    False.

    Restrictions can be not only set to items but also directly to variations.
    """
    item = VersionedForeignKey(
        Item,
        related_name='variations'
    )
    values = VersionedManyToManyField(
        PropertyValue,
        related_name='variations',
    )
    active = models.BooleanField(
        default=True,
        verbose_name=_("Active"),
    )
    default_price = models.DecimalField(
        decimal_places=2, max_digits=7,
        null=True, blank=True,
        verbose_name=_("Default price"),
    )

    class Meta:
        verbose_name = _("Item variation")
        verbose_name_plural = _("Item variations")

    def delete(self, *args, **kwargs):
        super().delete(*args, **kwargs)
        if self.item:
            self.item.event.get_cache().clear()

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.item:
            self.item.event.get_cache().clear()


class VariationsField(VersionedManyToManyField):
    """
    This is a ManyToManyField using the tixlcontrol.views.forms.VariationsField
    form field by default.
    """

    def formfield(self, **kwargs):
        from tixlcontrol.views.forms import VariationsField as FVariationsField
        from django.db.models.fields.related import RelatedField
        defaults = {
            'form_class': FVariationsField,
            # We don't need a queryset
            'queryset': ItemVariation.objects.none(),
        }
        defaults.update(kwargs)
        # If initial is passed in, it's a list of related objects, but the
        # MultipleChoiceField takes a list of IDs.
        if defaults.get('initial') is not None:
            initial = defaults['initial']
            if callable(initial):
                initial = initial()
            defaults['initial'] = [i.identity for i in initial]
        # Skip ManyToManyField in dependency chain
        return super(RelatedField, self).formfield(**defaults)


class BaseRestriction(Versionable):
    """
    A restriction is the abstract concept of a rule that limits the availability
    of Items or ItemVariations. This model is just an abstract base class to be
    extended by restriction plugins.
    """
    event = VersionedForeignKey(
        Event,
        on_delete=models.CASCADE,
        related_name="restrictions_%(app_label)s_%(class)s",
        verbose_name=_("Event"),
    )
    item = VersionedForeignKey(
        Item,
        blank=True, null=True,
        verbose_name=_("Item"),
        related_name="restrictions_%(app_label)s_%(class)s",
    )
    variations = VariationsField(
        'tixlbase.ItemVariation',
        blank=True,
        verbose_name=_("Variations"),
        related_name="restrictions_%(app_label)s_%(class)s",
    )

    class Meta:
        abstract = True
        verbose_name = _("Restriction")
        verbose_name_plural = _("Restrictions")

    def delete(self, *args, **kwargs):
        super().delete(*args, **kwargs)
        if self.event:
            self.event.get_cache().clear()

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.event:
            self.event.get_cache().clear()


class Quota(Versionable):
    """
    A quota is a "pool of tickets". It is there to limit the number of items
    of a certain type to be sold. For example, you could have a quota of 500
    applied to all your items (because you only have that much space in your
    building), and also a quota of 100 applied to the VIP tickets for
    exclusivity. In this case, no more than 500 tickets will be sold in total
    and no more than 100 of them will be VIP tickets (but 450 normal and 50
    VIP tickets will be fine).

    As always, a quota can not only be tied to an item, but also to specific
    variations. We follow the general rule here: If there are no variations
    speficied, the quota applies to all of them, and if there are variations
    specified, the quota applies to those.

    This object holds two fields, "order_cache" and "lock_cache", which are
    implementation specific and are considered private. It is planned that they
    are being used as a fallback solution if redis is not available.
    """
    event = VersionedForeignKey(
        Event,
        on_delete=models.CASCADE,
        related_name="quotas",
        verbose_name=_("Event"),
    )
    name = models.CharField(
        max_length=200,
        verbose_name=_("Name")
    )
    size = models.PositiveIntegerField(
        verbose_name=_("Total capacity")
    )
    items = VersionedManyToManyField(
        Item,
        verbose_name=_("Item"),
        related_name="quotas",
        blank=True
    )
    variations = VariationsField(
        ItemVariation,
        related_name="quotas",
        blank=True,
        verbose_name=_("Variations")
    )
    order_cache = models.ManyToManyField(
        'OrderPosition',
        blank=True
    )
    lock_cache = models.ManyToManyField(
        'CartPosition',
        blank=True
    )

    class Meta:
        verbose_name = _("Quota")
        verbose_name_plural = _("Quotas")

    def __str__(self):
        return self.name


class Order(Versionable):
    """
    An order is created when a user clicks 'buy' on his cart. It holds
    several OrderPositions and is connected to an user. It has an
    expiration date: If items run out of capacity, orders which are over
    their expiration date might be cancelled.

    Important: An order holds its total monetary value, as an order is a
    piece of 'history' and must not change due to a change in item prices.
    """

    STATUS_PENDING = "n"
    STATUS_PAID = "p"
    STATUS_EXPIRED = "e"
    STATUS_CANCELLED = "c"
    STATUS_CHOICE = (
        (STATUS_PAID, _("pending")),
        (STATUS_PENDING, _("paid")),
        (STATUS_EXPIRED, _("expired")),
        (STATUS_CANCELLED, _("cancelled")),
    )

    status = models.CharField(
        max_length=3,
        choices=STATUS_CHOICE,
        verbose_name=_("Status")
    )
    event = VersionedForeignKey(
        Event,
        verbose_name=_("Event")
    )
    user = models.ForeignKey(
        User, null=True, blank=True,
        verbose_name=_("User")
    )
    datetime = models.DateTimeField(
        auto_now_add=True,
        verbose_name=_("Date")
    )
    expires = models.DateTimeField(
        verbose_name=_("Expiration date")
    )
    payment_date = models.DateTimeField(
        verbose_name=_("Payment date")
    )
    payment_info = models.TextField(
        verbose_name=_("Payment information")
    )
    total = models.DecimalField(
        decimal_places=2, max_digits=10,
        verbose_name=_("Total amount")
    )

    class Meta:
        verbose_name = _("Order")
        verbose_name_plural = _("Orders")


class QuestionAnswer(Versionable):
    """
    The answer to a Question, connected to an OrderPosition or CartPosition
    """
    orderposition = models.ForeignKey('OrderPosition', null=True, blank=True)
    cartposition = models.ForeignKey('CartPosition', null=True, blank=True)
    question = VersionedForeignKey(Question)
    answer = models.TextField()


class OrderPosition(models.Model):
    """
    An OrderPosition is one line of an order, representing one ordered items
    of a specified type (or variation).

    Important: An OrderPosition holds its total monetary value, as an order is a
    piece of 'history' and must not change due to a change in item prices.
    """
    order = VersionedForeignKey(
        Order,
        verbose_name=_("Order")
    )
    item = VersionedForeignKey(
        Item,
        verbose_name=_("Item")
    )
    variation = VersionedForeignKey(
        ItemVariation,
        null=True, blank=True,
        verbose_name=_("Variation")
    )
    price = models.DecimalField(
        decimal_places=2, max_digits=10,
        verbose_name=_("Price")
    )
    answers = VersionedManyToManyField(
        Question,
        through=QuestionAnswer,
        verbose_name=_("Answers")
    )

    class Meta:
        verbose_name = _("Order position")
        verbose_name_plural = _("Order positions")


class CartPosition(models.Model):
    """
    A cart position is similar to a order line, except that it is not
    yet part of a binding order but just placed by some user in his or
    her cart. It therefore normally has a much shorter expiration time
    than an ordered position, but still blocks an item in the quota pool
    as we do not want to throw out users while they're clicking through
    the checkout process.
    """
    event = VersionedForeignKey(
        Event,
        verbose_name=_("Event")
    )
    user = models.ForeignKey(
        User, null=True, blank=True,
        verbose_name=_("User")
    )
    session = models.CharField(
        max_length=255, null=True, blank=True,
        verbose_name=_("Session key")
    )
    item = VersionedForeignKey(
        Item,
        verbose_name=_("Item")
    )
    variation = VersionedForeignKey(
        ItemVariation,
        null=True, blank=True,
        verbose_name=_("Variation")
    )
    total = models.DecimalField(
        decimal_places=2, max_digits=10,
        verbose_name=_("Price")
    )
    datetime = models.DateTimeField(
        verbose_name=_("Date")
    )
    expires = models.DateTimeField(
        verbose_name=_("Expiration date")
    )

    class Meta:
        verbose_name = _("Cart position")
        verbose_name_plural = _("Cart positions")
