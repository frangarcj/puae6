// Ffi inventory <directory with the built BizHawk assemblies>
// Prints what BizHawk's CoreInventory registers for the Amiga ("core NAME priority=N type=T ctor=P"), in the order of registration, and what the
// assembly records about the guest the PUAE6 core belongs with. Reflection only: the assemblies are net48 and are loaded as they were built.
using System.Collections;
using System.Reflection;

string directory = Path.GetFullPath(args[0]);
AppDomain.CurrentDomain.AssemblyResolve += (_, e) =>
{
	string candidate = Path.Combine(directory, new AssemblyName(e.Name).Name + ".dll");
	return File.Exists(candidate) ? Assembly.LoadFrom(candidate) : null;
};
var cores = Assembly.LoadFrom(Path.Combine(directory, "BizHawk.Emulation.Cores.dll"));
Type[] types;
try { types = cores.GetTypes(); }
catch (ReflectionTypeLoadException e) { types = e.Types.Where(t => t is not null).ToArray()!; }

var inventoryType = cores.GetType("BizHawk.Emulation.Cores.CoreInventory")!;
var inventory = Activator.CreateInstance(inventoryType, new object[] { new List<IEnumerable<Type>> { types } })!;
var found = (IEnumerable)inventoryType.GetMethod("GetCores")!.Invoke(inventory, new object[] { "Amiga" })!;
foreach (var core in found)
{
	var coreType = core.GetType();
	string name = (string)coreType.GetProperty("Name")!.GetValue(core)!;
	int priority = Convert.ToInt32(coreType.GetProperty("Priority")!.GetValue(core));
	var type = (Type)coreType.GetProperty("Type")!.GetValue(core)!;
	var ctor = (ConstructorInfo)coreType.GetProperty("CTor")!.GetValue(core)!;
	Console.WriteLine($"core {name} priority={priority} type={type.FullName} ctor={ctor.GetParameters()[0].ParameterType.Name}");
}

object Constant(string typeName, string field)
{
	var type = cores.GetType(typeName) ?? throw new InvalidOperationException($"{typeName} is not in the assembly");
	var member = type.GetField(field, BindingFlags.Public | BindingFlags.Static) ?? throw new InvalidOperationException($"{typeName}.{field} does not exist");
	return member.GetRawConstantValue() ?? "";
}
Console.WriteLine($"names PUAE6={Constant("BizHawk.Emulation.Cores.CoreNames", "PUAE6")} UAE={Constant("BizHawk.Emulation.Cores.CoreNames", "UAE")}");
var guest = cores.GetType("Puae6.Puae6Guest") ?? throw new InvalidOperationException("Puae6.Puae6Guest is not in the assembly");
Console.WriteLine($"pin {guest.GetProperty("BuiltWithSha256")!.GetValue(null)}");
